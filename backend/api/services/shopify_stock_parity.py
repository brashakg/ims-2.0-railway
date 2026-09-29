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
    that no MAPPED shop carries (a location two shops claim is mapped by
    neither -- the writer's own definition, inventory._mapped).
In-transit units count at neither location (owner accepted): the rule reads
the shelf, so they are in no row here either.

Tasks: ONE per shop (source_ref ``shopify-stock-parity-drift:<store_id>``) --
filed on drift, refreshed (description + payload) every night while it
drifts or still owes a SKU, completed when a later tick finds EVERY SKU the
task names either compared clean at that shop or GONE from the online
catalogue (deleted, or its Shopify item unmapped: nothing is left to measure,
so an empty catalogue closes every task too). payload.skus: a SKU leaves the
task only that way -- one Shopify skipped, that fell out of the capped sample
or whose IMS side was unknown is still owed, and the description names it.
A shop that leaves the mapped set (location cleared, claimed by two shops,
shop deactivated) has its task closed on EVERY tick that could read the shop
map, whether or not anything was compared: parity no longer compares it, and
the writer's own STORE_UNMAPPED / STORE_LOCATION_DUPLICATE task names what is
left. A task counts as filed / refreshed / closed only when the write
succeeded. The pre-PR-4 POOLED task (the bare
``shopify-stock-parity-drift`` ref) is never filed again;
scripts/close_pooled_parity_task.py closes the stuck one.

Contract (mirrors the rest of the Shopify bridge):
  * 100% FAIL-SOFT, end to end. No creds / no DB / Shopify error -> a
    structured reason, never a raise. It must NEVER take down SENTINEL.
  * READ-ONLY vs Shopify (single boundary: shopify_push._graphql, injectable).
    Half an answer is no answer: when any Shopify batch fails (a raise, no
    nodes, or top-level `errors` beside a partial nodes list), nothing is
    compared and no MAPPED shop's task is filed OR closed.
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

    Returns {compared, unknown, drift[], drift_count, max_delta, tolerance,
    clean_skus[]} where drift is [{sku, inventory_item_id, store_id, ims,
    shopify, delta}] sorted by the biggest delta first and clean_skus are the
    SKUs compared within tolerance (what may clear a drift task)."""
    tol = max(0, int(tolerance or 0))
    drift: List[Dict[str, Any]] = []
    clean: List[Any] = []
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
        if delta <= tol:
            clean.append(r.get("sku"))
        else:
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
        "clean_skus": clean,
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


def unbacked_units(
    variants: List[Dict[str, Any]],
    quantities: Dict[str, Dict[str, int]],
    levels: Dict[str, Dict[str, int]],
    mapped: Dict[str, str],
) -> Dict[str, Optional[int]]:
    """PURE: per SKU, the units Shopify lists that IMS does not back, counted
    LOCATION BY LOCATION -- ``max(0, Shopify - IMS)`` on every
    ``parity_rows`` pair (a mapped shop and its own location) plus EVERY unit
    at a location no mapped shop claims. Never a pooled sum: one shop's shelf
    (or unmapped Pune's) never backs a listing at another shop's location.

    Only SKUs whose item Shopify returned get a key. None = a location lists
    units against a shop IMS could not read and nothing else is known to be
    unbacked (unknown is never clean)."""
    claimed = set(mapped.values())
    out: Dict[str, int] = {}
    unknown = set()
    for r in parity_rows(variants, quantities, levels, mapped):
        shop = r["shopify_available"]
        if shop is None:
            continue
        if r["ims_available"] is None:
            if shop > 0:
                unknown.add(r["sku"])
            continue
        out[r["sku"]] = out.get(r["sku"], 0) + max(0, shop - int(r["ims_available"]))
    for v in variants:
        per_location = levels.get(v["inventory_item_id"])
        if per_location is not None:
            stray = sum(max(0, int(q)) for gid, q in per_location.items() if gid not in claimed)
            out[v["sku"]] = out.get(v["sku"], 0) + stray
    return {sku: (None if sku in unknown and not n else n) for sku, n in out.items()}


# ---------------------------------------------------------------------------
# Reads (IMS catalog sample, Shopify levels)
# ---------------------------------------------------------------------------


def _sample_variants(db) -> Optional[List[Dict[str, Any]]]:
    """EVERY IMS SKU (the spine ``products``, the rule's own SKU list) that
    maps to a Shopify inventory item through THE WRITER's resolver,
    online_catalog.inventory_items_for_skus (catalog_variants first, then the
    catalog_products ``ecom`` fallback), in spine order and UNCAPPED: the tick
    caps what it reads from Shopify, and the full list is what tells a SKU
    that is GONE (a task may drop it) from one that merely fell outside
    tonight's cap (still owed). Returns [{sku, inventory_item_id}]; None when
    a read failed (unknown, never "no online-mapped variants"). ponytail:
    resolves every spine SKU; cap the scan if the catalogue grows past a few
    thousand."""
    coll = _coll(db, "products")
    if coll is None:
        return None
    try:
        from .online_catalog import inventory_items_for_skus

        skus = list(dict.fromkeys(
            str(d.get("sku") or "").strip()
            for d in coll.find({"sku": {"$nin": [None, ""]}}, {"_id": 0, "sku": 1})
        ))
        items = inventory_items_for_skus(db, [s for s in skus if s])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] variant sample failed: %s", exc)
        return None
    return [{"sku": s, "inventory_item_id": items[s]} for s in skus if s in items]


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
            # Top-level `errors` beside a nodes list is a PARTIAL answer (a
            # node Shopify failed to resolve comes back null): half an answer.
            if not isinstance(nodes, list) or body.get("errors"):
                raise ValueError(f"no full nodes answer: {body.get('errors')}")
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


def _task_skus(task: Dict[str, Any]) -> List[str]:
    """The SKUs an open drift task is about: payload.skus (every drifted SKU),
    else the SKUs of its top-5 payload.drift rows."""
    payload = task.get("payload") or {}
    return list(payload.get("skus") or [d.get("sku") for d in payload.get("drift") or []])


def sync_drift_task(
    repo, store: Dict[str, Any], summary: Dict[str, Any], *, mapped_skus: Iterable[str]
) -> Optional[str]:
    """ONE shop's drift task, from that shop's own ``compare_location_parity``
    summary (source_ref ``shopify-stock-parity-drift:<store_id>``).
    ``mapped_skus``: EVERY online-mapped SKU in the catalogue tonight
    (_sample_variants, uncapped) -- a SKU the task names that is not in it is
    GONE (deleted, or its Shopify item unmapped): parity can never compare it
    again, so it is no longer owed.

      * drift, or an active task still owed a SKU
                          -> refresh every ACTIVE task's description + payload,
                             or file one when none is active (never a second);
      * 0 drift, every SKU the task names compared CLEAN tonight or is GONE
                          -> complete every active task (the drift cleared,
                             or nothing is left to measure);
      * otherwise         -> leave it alone.

    A SKU is still OWED when the task names it, it is still in the online
    catalogue and it did not compare clean tonight (Shopify skipped it, it
    fell out of the capped sample, or its IMS side was unknown): unknown is
    not clear. payload.skus carries tonight's drift plus every SKU still
    owed, and the description NAMES every one of them -- the text is what
    the store manager and the admin read (no task screen shows payload), so
    a SKU that keeps the task open is never missing from it. Returns "filed"
    | "refreshed" | "closed" only when EVERY write it made succeeded (the
    repository returns False / None on a rejected write, never raises), else
    None. Fail-soft."""
    from .task_triggers import active_tasks

    sid = str(store.get("store_id") or "")
    label = store.get("store_code") or store.get("store_name") or sid
    ref = _DRIFT_TASK_REF.format(store_id=sid)
    try:
        active = active_tasks(repo, ref)
        named = {s for t in active for s in _task_skus(t)}
        drift = summary.get("drift") or []
        drifted = {d.get("sku") for d in drift}
        owed = (named & set(mapped_skus)) - set(summary.get("clean_skus") or []) - drifted
        if drift or (active and owed):
            parts = []
            if drift:
                lines = ", ".join(
                    f"{d.get('sku')} (IMS {d.get('ims')} vs Shopify {d.get('shopify')})" for d in drift[:5]
                )
                parts.append(
                    f"{summary.get('drift_count')} online SKU(s) at {label}'s Shopify location drifted beyond "
                    f"tolerance {summary.get('tolerance')} unit(s); worst delta "
                    f"{summary.get('max_delta')}. Top: {lines}. The 01:00 / 09:00 IST pass and "
                    f"Push stock re-send only numbers IMS changed, so they never undo a change "
                    f"made on Shopify. Store manager: check each product named here on the "
                    f"shelf, then ask an ADMIN or SUPERADMIN (only they have the button) to "
                    f"open it and press Send to website to re-send IMS's numbers."
                )
            if owed:
                parts.append(
                    f"Still open from an earlier night, not compared tonight (Shopify returned "
                    f"nothing for the item, it was outside tonight's sample, or IMS could not "
                    f"read it): {', '.join(sorted(owed))}. Store manager: ask an ADMIN or "
                    f"SUPERADMIN to open each of these and check it is still on the website, "
                    f"then press Send to website."
                )
            parts.append(
                f"This task closes on the first night every product named here compares "
                f"clean at {label} or has left the online catalogue."
            )
            description = " ".join(parts)
            payload = {
                "store_id": sid,
                "drift": drift[:5],
                "skus": sorted(owed | drifted),
                "drift_count": summary.get("drift_count"),
                "max_delta": summary.get("max_delta"),
            }
            if active:
                # A list, not a generator: every task is written even after one fails.
                ok = all([repo.update(t.get("task_id"), {"description": description, "payload": payload})
                          for t in active])
                return "refreshed" if ok else None
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
        # A task that names no SKU (an old payload) closes only on a night
        # that compared something at this shop.
        if active and (named or summary.get("compared")):
            notes = (
                f"Auto-closed: every SKU this task named at {label} compared clean or left "
                f"the online catalogue ({summary.get('compared') or 0} SKU(s) compared)."
            )
            ok = all([repo.complete_task(t.get("task_id"), notes=notes) for t in active])
            return "closed" if ok else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] drift task sync failed for %s: %s", sid, exc)
    return None


def retire_unmapped_drift_tasks(repo, mapped: Dict[str, str]) -> List[str]:
    """Close the ACTIVE per-shop drift tasks of shops that are no longer in
    ``mapped`` (location cleared, one location claimed by two shops, shop
    deactivated or gone). Parity never compares such a shop again, so without
    this its task was never refreshed nor closed -- OPEN, then ESCALATED,
    forever: the stuck July pooled task all over again. The writer's own
    STORE_UNMAPPED / STORE_LOCATION_DUPLICATE task names what is left.
    Returns the store ids whose close was WRITTEN. Fail-soft -> []."""
    from .task_triggers import active_tasks

    prefix = _DRIFT_TASK_REF.format(store_id="")
    out: List[str] = []
    try:
        for t in active_tasks(repo, {"$regex": "^" + prefix}):
            sid = str(t.get("source_ref") or "")[len(prefix):]
            if sid and sid not in mapped:
                done = repo.complete_task(
                    t.get("task_id"),
                    notes=(
                        f"Auto-closed: {sid} no longer has a usable Shopify location "
                        "(cleared, shared with another shop, or the shop is inactive), "
                        "so its stock is no longer compared."
                    ),
                )
                if done and sid not in out:
                    out.append(sid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_PARITY] unmapped drift-task retire failed: %s", exc)
    return out


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
        from .online_stock_writeback import online_quantities_for_skus
        from .shopify_push.inventory import _mapped, _stores, unmapped_holders

        # The shop map first: a shop that left it has its task retired on
        # every return below, compared or not -- retiring needs the map and
        # nothing else (a raising shop list is a tick error: an unknown map
        # retires nothing).
        stores = _stores(db)
        mapped = _mapped(stores)
        repo = _task_repo(db)

        def not_compared(reason: str) -> Dict[str, Any]:
            closed = retire_unmapped_drift_tasks(repo, mapped) if repo is not None else []
            snap = {**base, "reason": reason, "tasks": {"filed": [], "refreshed": [], "closed": closed}}
            _store_snapshot(db, snap)
            return snap

        # Gate on creds so we don't mint tokens / call Shopify when unconfigured.
        try:
            from .shopify_push import _has_shopify_creds

            creds = _has_shopify_creds(db)
        except Exception as exc:  # noqa: BLE001
            return not_compared(f"creds check failed: {exc}")
        if not creds:
            return not_compared("shopify creds not configured")

        catalogue = _sample_variants(db)
        if catalogue is None:
            return not_compared("catalog read failed -- nothing compared, no mapped shop's task touched")
        variants = catalogue[: int(sample_limit)]
        base["sampled"] = len(variants)
        skus = [v["sku"] for v in variants]
        # The writer's own call (inventory.push_skus_stock): same rule, same
        # buffer -- so a non-zero safety buffer is never read as drift.
        quantities = online_quantities_for_skus(db, skus) or {}
        levels = await shopify_levels_by_item(
            db, [v["inventory_item_id"] for v in variants], graphql=graphql
        )
        if levels is None:
            return not_compared("shopify inventory read failed -- nothing compared, no mapped shop's task touched")

        cmp = compare_location_parity(parity_rows(variants, quantities, levels, mapped), tolerance)
        # The writer's own definition (inventory._mapped): a location two shops
        # claim is mapped by neither, so the units Shopify sells there are
        # unclaimed -- never silently "someone's".
        claimed = set(mapped.values())
        per_store = cmp["stores"]
        snapshot = {
            **base,
            "checked": True,
            # An empty catalogue is still a checked night: every task naming a
            # SKU is closed below (all of them are gone).
            "reason": None if variants else "no online-mapped variants",
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

        if repo is not None:
            mapped_skus = {v["sku"] for v in catalogue}
            for store in stores:
                sid = str(store.get("store_id") or "")
                if sid in mapped:
                    outcome = sync_drift_task(repo, store, per_store.get(sid) or {}, mapped_skus=mapped_skus)
                    if outcome:
                        snapshot["tasks"][outcome].append(sid)
            snapshot["tasks"]["closed"] += retire_unmapped_drift_tasks(repo, mapped)
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
