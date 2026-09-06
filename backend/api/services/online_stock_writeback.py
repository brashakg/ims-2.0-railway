"""
IMS 2.0 - IMS -> Shopify stock write-back (oversell guard)   [Council B11]
==========================================================================
IMS is the inventory MASTER. When a unit leaves a shop's shelf (POS sale,
online-order claim, transfer ship, quarantine, stock-count write-off, any
ledger transition off on-hand) the website must not be able to sell it. This
module recomputes THE ONE RULE for the affected SKUs and hands the rows to THE
ONE WRITER (shopify_push.inventory.push_skus_stock).

THE ONE RULE (owner ruling 2026-09-06, per-store Shopify locations):

    quantity Shopify shows for SKU s at location L
      = recommend_allocation( on_hand(s, store(L)), safety_buffer )

``online_quantities_for_skus`` -> ``{sku: {store_id: qty}}`` over every
ACTIVE PHYSICAL shop (stores_util.physical_stores; ONLINE stores are never in
the loop, so a phantom unit on BV-ONLINE-01 counts nowhere), each shop read
through the STRICT ``_on_hand_for_skus(db, skus, store_id)``: a shop whose
read failed is ABSENT from every SKU's inner dict (unknown is never written
as 0; every other shop's true numbers still go out). A SKU in a SUPERADMIN
online-blocked collection (online_block.blocked_skus) is 0 at every shop --
part of the rule, so the POS door and the 01:00 / 09:00 pass agree.

Flow on a sale:
  1. The POS create-order path flips serialized stock_units to SOLD, then calls
     writeback_after_sale(db, items_data, store_id). Every other door that
     removes availability calls writeback_after_restock /
     writeback_after_units_left the same way (fire-and-forget, fail-soft).
  2. writeback_skus resolves the SKUs' Shopify InventoryItem gids
     (online_catalog.inventory_items_for_skus). No mapping -> NO-OP for a
     product that simply isn't online, but a SKU that IS listed online and
     cannot be targeted is a GUARD GAP: logged loudly + a deduped SYSTEM task.
  3. push_skus_stock writes one ABSOLUTE row per (mapped shop, SKU) -- an
     explicit 0 included -- and updates the SAME ecom.online_stock baseline the
     scheduled pass diffs against, so the next 01:00 / 09:00 pass is a noop.

Contract (mirrors the rest of the consolidation bridge):
- 100% FAIL-SOFT. A Shopify/Mongo failure is caught + logged and NEVER
  propagates into (or slows/blocks) the sale. The write-back is best-effort.
- GATED by the shopify_push gates (IMS_SHOPIFY_WRITES + DISPATCH_MODE +
  creds). DARK -> a SIMULATED plan, zero network.
- The async push is fire-and-forget (scheduled on the running loop) so the HTTP
  round-trip is fully off the request path. If no loop is running (sync /
  test context) it runs inline and still never raises.

Returns / restock: when stock goes back UP the same path re-pushes the higher
count, so the online listing recovers too.
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


def _online_store_ids(db) -> List[str]:
    """Store ids that are ONLINE (pooled + stockless) and therefore must NEVER
    contribute to the pooled on-hand we publish to Shopify.

    An AVAILABLE unit parked on BV-ONLINE-01 / WO-ONLINE-01 is unpickable: the
    online store has no shelf and POS is blocked on it (PR #941), so counting it
    would publish availability that no shop can actually ship. The known-id
    allow-list from services.stores_util is the floor (it can never be empty, so
    this never degrades to "exclude nothing"); the `stores` collection is then
    consulted for any further store_type == ONLINE rows. Fully fail-soft -- a
    lookup failure falls back to the known ids rather than raising into the
    STRICT on-hand contract below."""
    from .stores_util import KNOWN_ONLINE_STORE_IDS, ONLINE_STORE_TYPE

    ids = set(KNOWN_ONLINE_STORE_IDS)
    if db is None:
        return sorted(ids)
    try:
        coll = db.get_collection("stores")
        if coll is not None:
            for row in coll.find(
                {"store_type": ONLINE_STORE_TYPE}, {"_id": 0, "store_id": 1}
            ):
                sid = str((row or {}).get("store_id") or "").strip()
                if sid:
                    ids.add(sid)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[STOCK_WRITEBACK] online-store lookup fell back to known ids: %s", exc
        )
    return sorted(ids)


def _on_hand_for_skus(db, skus: List[str], store_id: Optional[str]) -> Dict[str, int]:
    """Map each SKU -> IMS on-hand (AVAILABLE stock_units) at ONE store -- the
    per-shop half of THE ONE RULE (online_quantities_for_skus calls this once
    per physical shop). store_id=None is the POOLED count across all PHYSICAL
    stores and is kept ONLY for shopify_stock_parity._pooled_availability
    until PR 4 rewrites parity per location; no writer reads it.

    STRICT failure contract (audit round-2 P1): this feeds an ABSOLUTE stock
    WRITER, so an aggregate failure must surface as {} (UNKNOWN -> the caller's
    batch abort), never as default-0. inventory._on_hand_by_product swallows
    aggregate exceptions internally ('except: pass' -> {}/PARTIAL without
    raising), which would let a Mongo blip default every spine-resolved SKU to
    0 and write absolute 0 live -- so the SAME canonical pipeline (identical
    match/group, its status half from item_events.on_hand_match) is run
    INLINE here: ANY exception, including one raised MID-ITERATION after some
    rows were yielded, discards the partial result and returns {}. A pid absent
    from a SUCCESSFULLY completed aggregate legitimately means zero on-hand and
    only then defaults to 0."""
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
    # A product IMS stopped selling (is_active False -- the retire hook's
    # marker) lists 0 online whatever its shelves hold: written as 0, the
    # oversell-guard contract, never "unknown". A MISSING flag is active (the
    # purchasable rule every other reader applies) -- `is False`, never
    # `not`. This is what keeps a deactivated size variant off sale after
    # online_delist wrote its 0, and what brings it back on reactivation.
    inactive: set = set()
    try:
        for p in prod_coll.find(
            {"sku": {"$in": list(skus)}},
            {"_id": 0, "product_id": 1, "sku": 1, "is_active": 1},
        ):
            sku = str(p.get("sku") or "").strip()
            pid = p.get("product_id")
            if sku and pid and sku not in sku_to_pid:
                sku_to_pid[sku] = pid
                if p.get("is_active") is False:
                    inactive.add(sku)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] sku->product lookup failed: %s", exc)
        return {}
    if not sku_to_pid:
        return {}

    # Canonical on-hand pipeline, STRICT (same match as
    # inventory._on_hand_by_product, without its exception swallow). The status
    # half is item_events.on_hand_match -- it used to be copied out here, and a
    # copy is how the same unit came to be on hand to one reader and gone to
    # the next. The cursor is consumed INSIDE the try so a mid-iteration
    # failure also discards the partial dict.
    try:
        from .item_events import on_hand_match

        stock_coll = db.get_collection("stock_units")
        if stock_coll is None:
            return {}
        match: Dict[str, Any] = {
            "product_id": {"$in": list(sku_to_pid.values())},
            **on_hand_match(),
        }
        if store_id:
            match["store_id"] = store_id
        else:
            # POOLED count -- PARITY ONLY (shopify_stock_parity._pooled_
            # availability) until PR 4 compares per location; deleted with it.
            # Every PHYSICAL shop's on-hand, never a unit stranded on a
            # stockless ONLINE store.
            online_ids = _online_store_ids(db)
            if online_ids:
                match["store_id"] = {"$nin": online_ids}
        on_hand_by_pid: Dict[str, int] = {}
        for row in stock_coll.aggregate(
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
            on_hand_by_pid[row.get("_id")] = int(row.get("n", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[STOCK_WRITEBACK] on-hand aggregate failed (STRICT -> unknown, "
            "batch will abort rather than write 0): %s",
            exc,
        )
        return {}

    # The aggregate COMPLETED: a pid it omitted genuinely has zero on-hand.
    out: Dict[str, int] = {}
    for sku, pid in sku_to_pid.items():
        out[sku] = 0 if sku in inactive else int(on_hand_by_pid.get(pid, 0) or 0)
    return out


def _blocked_online(db, skus: List[str]) -> set:
    """The SKUs in a SUPERADMIN online-blocked collection. Fail-soft: an
    error blocks nothing (never wrongly delists a sellable SKU)."""
    try:
        from . import online_block

        return set(online_block.blocked_skus(db, list(skus)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] block lookup skipped: %s", exc)
        return set()


def online_quantities_for_skus(
    db, skus: List[str], *, safety_buffer: Optional[int] = None
) -> Dict[str, Dict[str, int]]:
    """THE online quantity rule -- what the website lists for a SKU at EACH
    shop's Shopify location: ``{sku: {store_id: recommend_allocation(on_hand
    at that shop, safety_buffer)}}`` for every ACTIVE PHYSICAL shop
    (stores_util.physical_stores -- mapped or not; the writer decides what to
    do with an unmapped holder). ONLINE stores are excluded structurally: they
    are never in the loop. The buffer applies PER SHOP (Shopify routes per
    shelf). A SKU blocked from online sale (online_block) is 0 at EVERY shop
    whatever the shelves hold -- the block is part of the rule, not a caller's
    override, so no pass can write the shelf count back after the POS door
    wrote 0.

    STRICT, per shop: a shop whose ``_on_hand_for_skus`` call returned ``{}``
    for a non-empty request is ABSENT from every SKU's inner dict (unknown ->
    never written as 0; the other shops still go out). A SKU absent from the
    result has no spine row (unknown). ``{}`` for a non-empty request means
    the shop list could not be read or EVERY shop failed -- the whole-batch
    abort. Zero physical shops -> ``{sku: {}}`` (known, nothing to list).

    ponytail: one aggregate per shop (six small indexed reads twice a day
    plus one per sale) -- the strict fake refuses a composite $group and the
    per-shop STRICT contract comes free; one composite aggregate + a
    strict_fakes extension if a pass ever measures slow."""
    clean = [s for s in dict.fromkeys(skus or []) if s]
    if db is None or not clean:
        return {}
    try:
        from .stores_util import physical_stores

        stores = physical_stores(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK_WRITEBACK] shop list unknown (STRICT -> batch abort): %s", exc)
        return {}
    from . import stock_allocation

    buf = _safety_buffer(db) if safety_buffer is None else max(0, int(safety_buffer))
    out: Dict[str, Dict[str, int]] = {}
    known_store = False
    for store in stores:
        sid = str(store.get("store_id") or "").strip()
        if not sid:
            continue
        on_hand = _on_hand_for_skus(db, clean, sid)
        if not on_hand:
            logger.warning(
                "[STOCK_WRITEBACK] on-hand UNKNOWN at %s -- that shop is written "
                "nowhere this pass", sid,
            )
            continue
        known_store = True
        for sku, q in on_hand.items():
            out.setdefault(sku, {})[sid] = stock_allocation.recommend_allocation(q, buf)
    if stores and not known_store:
        return {}
    sids = [str(s.get("store_id") or "").strip() for s in stores]
    for sku in _blocked_online(db, clean):
        out[sku] = {sid: 0 for sid in sids if sid}
    if not stores:
        return {sku: {} for sku in clean}
    return out


async def writeback_skus(
    db,
    skus: List[str],
    store_id: Optional[str] = None,
    *,
    source: str = "sale",
    safety_buffer: Optional[int] = None,
) -> Dict[str, Any]:
    """Core: recompute THE ONE RULE for each SKU that maps to an online
    inventory item and hand the per-shop rows to THE ONE WRITER
    (shopify_push.inventory.push_skus_stock). Gated + fail-soft. Returns a
    summary dict (pushed / skipped / failed / simulated / unmapped_stores).
    NEVER raises. Nothing here computes a quantity: the online block, the
    buffer and the per-shop on-hand all live in the rule.

    ``store_id`` is CONTEXT ONLY (recorded on the summary for logging);
    quantities are per shop by construction, so the shop that lost the unit
    is the one whose location goes down and every other shop's row is re-sent
    unchanged.

    Safety rules for the absolute write:
    - A SKU with no Shopify InventoryItem mapping is skipped -- and when it IS
      sellable online (PUBLISHED / live variant) that is a GUARD GAP alerted
      LOUDLY (_alert_unmapped_online), never a silent fake success.
    - UNKNOWN on-hand is NEVER written as 0: a shop whose read failed is
      written nowhere (unknown_stores), an entirely-unknown batch aborts with
      a WARNING + not-ok sync_runs row. A shop PRESENT with on-hand 0 still
      gets 0 -- that IS the oversell guard.
    - A shop that HOLDS a listed unit but has no Shopify location is reported
      (STORE_UNMAPPED, not-ok run, deduped task); the mapped shops are still
      written.
    """
    summary: Dict[str, Any] = {
        "source": source,
        "store_id": store_id,  # context only -- quantities are per shop
        "candidates": 0,
        "pushed": 0,
        "simulated": 0,
        "skipped_no_mapping": 0,
        "skipped_no_onhand": 0,
        "failed": 0,
        "unmapped_online": 0,
        "unmapped_stores": [],
        "unknown_stores": [],
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
        from .shopify_push.inventory import push_skus_stock
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] deps unavailable: %s", exc)
        return summary

    summary["online_configured"] = online_catalog.online_mapping_available(db)

    # 1. Resolve the Shopify inventory-item targets from the IMS Mongo mapping
    #    (the ONE target reader). A SKU with no target is skipped -- and
    #    checked below for the online-but-unmapped guard gap.
    targets = online_catalog.inventory_items_for_skus(db, distinct)
    if not targets:
        summary["skipped_no_mapping"] = len(distinct)
        _alert_unmapped_online(db, distinct, summary)
        _record_run(db, summary)
        return summary

    # 2. THE ONE rule, per shop, for the targeted SKUs.
    quantities = online_quantities_for_skus(
        db, list(targets.keys()), safety_buffer=safety_buffer
    )
    unmapped = [s for s in distinct if s not in targets]
    summary["skipped_no_mapping"] = len(unmapped)
    if not quantities:
        # UNKNOWN on-hand for the whole batch (shop list / lookup / aggregate
        # failed or no spine rows). An absolute stock WRITER must never fail
        # soft to 0 -- writing 0 would delist every sold SKU that is
        # physically in stock. Abort loudly.
        summary["skipped_no_onhand"] = len(targets)
        logger.warning(
            "[STOCK_WRITEBACK] on-hand UNKNOWN for all %d targeted SKU(s) at "
            "every shop -- aborting the push batch; NOT writing 0 to the live "
            "listing. SKUs: %s",
            len(targets),
            ", ".join(sorted(targets.keys())[:20]),
        )
        # The abort must not swallow the guard-gap check for the UNMAPPED
        # subset (round-2 rider): a sellable-online SKU with no mapping still
        # alerts even when this batch aborted on unknown on-hand.
        if unmapped:
            _alert_unmapped_online(db, unmapped, summary)
        _record_run(db, summary)
        return summary

    # 3. THE ONE writer: one row per (mapped shop, SKU), explicit 0 included
    #    (a SUPERADMIN-blocked SKU arrives from the rule as 0 everywhere).
    res = await push_skus_stock(db, list(targets.keys()), quantities=quantities, source=source)
    summary["unmapped_stores"] = list(res.get("unmapped_stores") or [])
    summary["unknown_stores"] = list(res.get("unknown_stores") or [])
    summary["skipped_no_onhand"] = sum(1 for s in targets if s not in quantities)
    written_skus = [s for s, rows in (res.get("quantities") or {}).items() if rows]
    if res.get("mode") == "LIVE":
        summary["pushed"] = len(written_skus) if res.get("set") else 0
        summary["failed"] = len(res.get("errors") or [])
    else:
        summary["simulated"] = len(written_skus)
    if res.get("code"):
        summary["code"] = res["code"]

    # SKUs with no online mapping (present in distinct but not targets).
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
        or summary.get("unmapped_stores")
        or summary.get("unknown_stores")
    ):
        return
    unmapped_online = int(summary.get("unmapped_online", 0) or 0)
    skipped_no_onhand = int(summary.get("skipped_no_onhand", 0) or 0)
    unmapped_stores = list(summary.get("unmapped_stores") or [])
    unknown_stores = list(summary.get("unknown_stores") or [])
    errors = []
    if summary.get("failed"):
        errors.append(f"{summary.get('failed')} push(es) failed")
    if unmapped_stores:
        names = ", ".join(
            str(s.get("store_code") or s.get("store_name") or s.get("store_id"))
            for s in unmapped_stores
        )
        errors.append(
            f"STORE_UNMAPPED: {names} hold listed stock with no Shopify location "
            f"(invisible online until mapped)"
        )
    if unknown_stores:
        errors.append(
            f"on-hand UNKNOWN at {', '.join(unknown_stores)} (written nowhere, "
            f"never as 0)"
        )
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
                    and not unmapped_stores
                    and not unknown_stores
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
    blocked or slowed. store_id is context only; quantities are per shop by
    construction (the selling shop's own location goes down). NEVER raises."""
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


def writeback_after_units_left(
    db, product_ids: List[str], store_id: Optional[str], *, source: str
) -> None:
    """Fail-soft entrypoint for the doors that know the PRODUCT, not the SKU
    (transfer ship, quarantine-in, stock-count write-off, the item_events
    ledger's on-hand -> not-on-hand hook): resolve the spine SKUs and dispatch
    the same write-back. NEVER raises."""
    try:
        pids = [str(p) for p in dict.fromkeys(product_ids or []) if p]
        handle = _resolve_db(db)
        if not pids or handle is None:
            return
        skus = [
            str(p.get("sku") or "").strip()
            for p in handle.get_collection("products").find(
                {"product_id": {"$in": pids}}, {"_id": 0, "sku": 1}
            )
        ]
        writeback_after_restock(handle, [s for s in skus if s], store_id, source=source)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[STOCK_WRITEBACK] after-units-left skipped: %s", exc)
