"""
IMS 2.0 - Shopify LIVE-PRODUCT SYNC  (owner ruling 2026-09-06)
================================================================
"anytime a product that has already been pushed to shopify, edited or changed
in our ims, it should automatically reflect on shopify. if needed a sync
everyday twice should be done. one before store opening around 9 am and next
at 1 am." -- plus "a dedicated button to sync manually whenever needed by
humans" and "a module/section in settings for superadmin to tweak it".

WHAT THIS IS
------------
A scheduled (default 01:00 + 09:00 IST, daily; SUPERADMIN-tunable through the
policy registry) and manually-pressable sweep that re-pushes every catalogue
product that is ALREADY ON SHOPIFY (carries an ``ecom.shopify_product_id``)
and has been edited since (``ecom.locally_modified``).

WHAT THIS IS NOT
----------------
It never publishes a product for the FIRST time. A dirty product with no
Shopify gid is counted as ``awaiting_first_publish`` and left alone: the older
"flag only" ruling still governs new products -- a human presses Publish.

ONE ENGINE, ONE SELECTION
-------------------------
The push itself is ``shopify_push.push_product`` behind the SAME three gates as
every other push door (IMS_SHOPIFY_WRITES + SHOPIFY_DISPATCH_MODE=live + creds):
DARK => a SIMULATED plan and zero network. The product-sweep core that
``POST /online-store/push/all-pending`` runs lives HERE (``select_dirty_products``
+ ``push_product_docs``) and the router calls it, so the manual sweep and the
scheduled sync cannot drift apart (the repo's dominant defect class is one rule
implemented twice).

THE SETTINGS (policy registry, SUPERADMIN, global scope)
--------------------------------------------------------
  shopify.live_sync.enabled               bool, default True
  shopify.live_sync.slots                 ["HH:MM", ...] IST, default 01:00 + 09:00
  shopify.live_sync.max_products_per_run  int, default 200
Read on EVERY tick through policy_engine.get_policy (no restart).

THE SCHEDULE
------------
The agents scheduler calls ``scheduled_tick`` every POLL_MINUTES, round the
clock, under BOTH scheduler modes (APScheduler interval job / asyncio loop).
Each tick asks "is an IST slot due?": a slot is due from its HH:MM until
SLOT_GRACE_MINUTES later, so a worker that was down at 01:00 sharp catches up
at 01:05 / 01:10 / ... and a slot missed by more than the grace window is
skipped (never run hours late into the trading day).

THE RUN LOCK
------------
One IST slot runs ONCE, however many workers are alive and however many ticks
land inside the grace window. The claim is an atomic upsert on
``online_sync_runs`` keyed by ``lock_key = "<job>:<IST date> <HH:MM>"`` (a
sparse UNIQUE index, see database/connection.ensure_indexes); the loser sees the
existing doc (or a DuplicateKeyError under a true race) and skips.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..utils.ist import IST, now_ist
from . import policy_registry as reg
from . import shopify_push

logger = logging.getLogger(__name__)

JOB_ID = "shopify-live-sync"
SCHEDULER_ACTOR = "scheduler:shopify-live-sync"
MANUAL_SLOT = "manual"
# Tick cadence, in both scheduler modes. A slot missed at :00 is caught by the
# next tick inside the grace window; the lock keeps it to one run.
POLL_MINUTES = 5
# How long after its HH:MM a slot stays claimable ("a missed slot runs on the
# next tick within 55 minutes, never twice").
SLOT_GRACE_MINUTES = 55
RUNS_COLLECTION = "online_sync_runs"


# ---------------------------------------------------------------------------
# Shared helpers (moved here from routers/online_store_push.py so the router and
# the scheduled sync run the SAME code; the router re-imports them by name).
# ---------------------------------------------------------------------------


def connected_db():
    """Underlying DB object (real pymongo Database or seeded MockDatabase) when
    connected, else None. Subscript access (db[name]) works on both."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def write_push_audit(result: Dict[str, Any], current_user: dict) -> None:
    """Write a chained audit row for a push ATTEMPT (live OR dry-run). Captures
    the mode + entity + target + ok + shopify_id + error so the owner has an
    immutable record of every push. Fail-soft: any audit error is swallowed so it
    can never undo/block the push (mirrors online_store_images._write_audit)."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is None:
            return
        mode = result.get("mode")
        ok = result.get("ok")
        # A failed push (live or dry-run) is a WARNING so it surfaces in the
        # warnings/critical audit views; a clean push is INFO.
        severity = "INFO" if ok else "WARNING"
        audit.create(
            {
                "action": "ONLINE_STORE_PUSH",
                "entity_type": result.get("entity"),
                "entity_id": result.get("target_id"),
                "user_id": current_user.get("user_id"),
                "severity": severity,
                # Stamp `timestamp` (the field the rest of audit_logs + its
                # (action, timestamp) compound index sort on -- see
                # database/connection.ensure_indexes). AuditRepository.create
                # only sets created_at/updated_at, so without this every
                # ONLINE_STORE_PUSH row was invisible to the timestamp-sorted
                # Activity Log views AND forced the history read onto an
                # unindexed created_at sort. Set to the audit-write instant.
                "timestamp": datetime.now(),
                "details": {
                    "mode": mode,
                    "push_action": result.get("action"),
                    "ok": ok,
                    "shopify_id": result.get("shopify_id"),
                    "error": result.get("error"),
                    "reason": result.get("reason"),
                    "code": result.get("code"),
                    # What IMS did to cause an automatic take-down
                    # (deleted / deactivated) -- services/online_delist.
                    "trigger": result.get("trigger"),
                    # The publish side channel keeps the RAW vendor error
                    # (`error` above is the plain-language line).
                    "publication": result.get("publication"),
                },
            }
        )
    except Exception:  # noqa: BLE001 -- audit must never break the push
        pass


def all_docs(db, name: str) -> List[Dict]:
    """All docs in a collection (Mongo _id stripped). Fail-soft -> []."""
    try:
        rows = list(db[name].find({}, {"_id": 0}))
        for r in rows:
            if isinstance(r, dict):
                r.pop("_id", None)
        return rows
    except Exception:  # noqa: BLE001
        return []


def variants_for_product(db, product: Dict) -> List[Dict]:
    """All catalog_variants whose parent is this product (by parent_product_id or
    parent_sku). Fail-soft -> []."""
    try:
        from database.repositories import CatalogVariantRepository

        repo = CatalogVariantRepository(db["catalog_variants"])
        pid = product.get("id") or product.get("product_id")
        rows = repo.list_by_parent(pid) if pid else []
        if not rows and product.get("sku"):
            # Fall back to parent_sku linkage when the id link wasn't set.
            rows = repo.find_many({"parent_sku": product.get("sku")})
        return rows or []
    except Exception:  # noqa: BLE001
        return []


def select_dirty_products(db) -> Tuple[List[Dict], int]:
    """The product push queue: every catalog_products doc whose ecom sub-doc is
    dirty (``locally_modified``), MINUS the rows a human took down by hand.

    TAKEN DOWN BY HAND -> a sweep NEVER puts it back. The take-down writes
    DRAFT and clears the flag, but any catalogue edit re-queues the row and
    the mapper sends everything except ARCHIVED as ACTIVE -- so without this
    the owner pulls a bad listing, someone edits it to fix it, and the next
    sweep (or the 01:00 sync, unattended) re-lists it mid-fix. Only an
    explicit per-product press clears the marker (a successful publish does).

    Returns (dirty_docs, taken_down_skipped)."""
    dirty: List[Dict] = []
    taken_down_skipped = 0
    for doc in all_docs(db, "catalog_products"):
        ecom = doc.get("ecom")
        if not (ecom and ecom.get("locally_modified")):
            continue
        if ecom.get("taken_down_at"):
            taken_down_skipped += 1
            continue
        dirty.append(doc)
    return dirty, taken_down_skipped


async def push_product_docs(
    db,
    docs: List[Dict],
    *,
    current_user: dict,
    max_results: Optional[int] = None,
    max_sent: Optional[int] = None,
) -> Dict[str, Any]:
    """Push a list of product docs through ``shopify_push.push_product`` (DARK =>
    SIMULATED) with one audit row each. The shared core of the manual sweep and
    the scheduled live sync.

    The block classification is hoisted ONCE for the whole batch (findings
    #17 + #20): a BLOCKED product is excluded (never consumes a slot, never
    writes a junk MODE_BLOCKED audit row); non-blocked rows are pushed with a
    precomputed blocked=False so push_product does not re-scan the block
    config per product. If the block CONFIG is unreadable (verifiable=False)
    we pass blocked=None so push_product FAILS CLOSED per row (finding #18).

    ``max_results`` caps the rows attempted (every result counts);
    ``max_sent`` caps the rows that reached Shopify -- a photo-less REFUSAL
    never did, so it does not count against it (the press cap semantics).

    Returns {results, blocked_skipped, sent, cap_reached, limit_reached}."""
    from . import online_block

    skus = [d.get("sku") for d in docs if d.get("sku")]
    blocked_set, block_verifiable = online_block.classify_blocked_skus(db, skus)
    precomputed = False if block_verifiable else None

    results: List[Dict[str, Any]] = []
    blocked_skipped = 0
    sent = 0
    cap_reached = False
    limit_reached = False
    for doc in docs:
        if max_results is not None and len(results) >= max_results:
            limit_reached = True
            break
        if max_sent is not None and sent >= max_sent:
            cap_reached = True
            break
        if block_verifiable and doc.get("sku") in blocked_set:
            blocked_skipped += 1
            continue
        variants = variants_for_product(db, doc)
        data = (
            await shopify_push.push_product(db, doc, variants, blocked=precomputed)
        ).to_dict()
        write_push_audit(data, current_user)
        results.append(data)
        if data.get("reason") != "no_photo":
            sent += 1
    return {
        "results": results,
        "blocked_skipped": blocked_skipped,
        "sent": sent,
        "cap_reached": cap_reached,
        "limit_reached": limit_reached,
    }


# ---------------------------------------------------------------------------
# Settings (read every tick)
# ---------------------------------------------------------------------------


def live_sync_config() -> Dict[str, Any]:
    """{enabled, slots, max_products_per_run} from the policy registry (global
    scope). Fail-soft to the registry defaults: a settings-store hiccup must
    never turn the sync off OR let a corrupt slot list crash the tick."""
    try:
        from . import policy_engine as pe

        enabled = bool(pe.get_policy(reg.LIVE_SYNC_ENABLED_KEY))
        raw_slots = pe.get_policy(reg.LIVE_SYNC_SLOTS_KEY)
        limit = int(pe.get_policy(reg.LIVE_SYNC_MAX_KEY))
    except Exception:  # noqa: BLE001
        enabled, raw_slots, limit = True, list(reg.LIVE_SYNC_DEFAULT_SLOTS), 200
    try:
        slots = reg.validate_live_sync_slots(raw_slots)
    except ValueError:
        logger.warning("[LIVE-SYNC] stored slots %r invalid -- using defaults", raw_slots)
        slots = list(reg.LIVE_SYNC_DEFAULT_SLOTS)
    return {"enabled": enabled, "slots": slots, "max_products_per_run": max(1, limit)}


# ---------------------------------------------------------------------------
# IST slot arithmetic
# ---------------------------------------------------------------------------


def _as_ist(now: Optional[datetime]) -> datetime:
    """``now`` as a tz-aware IST datetime. None -> IST now. A NAIVE instant is
    the UTC box clock (BUG-104: Railway runs UTC), never IST wall-clock."""
    if now is None:
        return now_ist()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(IST)


def _slot_instants(slots: List[str], ist_day) -> List[Tuple[str, datetime]]:
    """(HH:MM, aware IST instant) for every slot on the IST calendar day."""
    out = []
    for face in slots:
        hh, mm = (int(p) for p in face.split(":"))
        out.append((face, datetime(ist_day.year, ist_day.month, ist_day.day, hh, mm, tzinfo=IST)))
    return out


def _slot_key(face: str, at: datetime) -> str:
    return f"{at.date().isoformat()} {face}"


def due_slot(slots: List[str], now: Optional[datetime] = None) -> Optional[str]:
    """The slot key ("YYYY-MM-DD HH:MM", IST) whose grace window ``now`` falls
    inside -- from the slot's HH:MM up to SLOT_GRACE_MINUTES after it -- or
    None. Yesterday's slots are checked too, so a 23:30 slot is still due at
    00:10. Every tick inside the window maps to the SAME key, which is what
    lets a late tick catch a missed slot without ever running it twice."""
    n = _as_ist(now)
    grace = timedelta(minutes=SLOT_GRACE_MINUTES)
    for day in (n.date() - timedelta(days=1), n.date()):
        for face, at in _slot_instants(slots, day):
            if at <= n < at + grace:
                return _slot_key(face, at)
    return None


def next_slot(slots: List[str], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The next configured slot strictly after ``now``: aware ISO instant plus a
    human IST label (rendered server-side so no browser clock can mis-state
    it). None when no slots are configured."""
    n = _as_ist(now)
    cands = [
        (face, at)
        for day in (n.date(), n.date() + timedelta(days=1))
        for face, at in _slot_instants(slots, day)
        if at > n
    ]
    if not cands:
        return None
    face, at = min(cands, key=lambda c: c[1])
    return {
        "slot": _slot_key(face, at),
        "at": at.isoformat(),
        "label": at.strftime("%a %d %b %Y, %H:%M IST"),
    }


# ---------------------------------------------------------------------------
# Run ledger + the slot lock
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _open_run(db, *, trigger: str, actor: str, slot: str) -> Optional[Dict[str, Any]]:
    """Insert the run doc. For a scheduled slot this IS the lock: an atomic
    upsert on the unique ``lock_key``; if a doc for the slot already exists
    (another worker, or an earlier tick this window) the claim is LOST and None
    is returned. Manual runs never lock -- every press is its own run.

    ponytail: under the no-Mongo MockCollection (local dev only) the upsert
    returns the seeded doc instead of None, so a scheduled claim always reads
    as lost there; prod is real Mongo and tests use the strict fake."""
    doc: Dict[str, Any] = {
        "run_id": uuid.uuid4().hex,
        "job": JOB_ID,
        "trigger": trigger,
        "actor": actor,
        "slot": slot,
        "started_at": _utc_now(),
        "finished_at": None,
        "status": "running",
    }
    coll = db[RUNS_COLLECTION]
    if slot == MANUAL_SLOT:
        coll.insert_one(dict(doc))
        return doc
    lock_key = f"{JOB_ID}:{slot}"
    try:
        before = coll.find_one_and_update(
            {"lock_key": lock_key},
            {"$setOnInsert": doc},
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
    except DuplicateKeyError:
        # Two workers upserted the same slot in the same instant; the unique
        # index let exactly one in. We are the other one.
        return None
    if before is not None:
        return None
    doc["lock_key"] = lock_key
    return doc


def _iso(value: Any) -> Any:
    """Datetimes -> aware ISO strings. A doc read back from Mongo carries NAIVE
    UTC; stamp the offset so the browser cannot mistake it for local time."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _serialise_run(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    return {k: _iso(v) for k, v in doc.items() if k not in ("_id", "lock_key")}


def last_run(db) -> Optional[Dict[str, Any]]:
    """The most recent run summary (scheduled or manual), JSON-ready. None when
    nothing has ever run. Cursor sort (not find_one(sort=)) so it reads the same
    on pymongo, the strict fake and the no-Mongo MockCollection. Fail-soft."""
    try:
        rows = list(db[RUNS_COLLECTION].find({"job": JOB_ID}).sort([("started_at", -1)]).limit(1))
    except Exception:  # noqa: BLE001
        return None
    return _serialise_run(rows[0]) if rows else None


def status_block(db) -> Dict[str, Any]:
    """What GET /online-store/push/status reports about the live sync: the
    effective settings, the last run and the next scheduled slot."""
    cfg = live_sync_config()
    nxt = next_slot(cfg["slots"]) if cfg["enabled"] else None
    return {
        "enabled": cfg["enabled"],
        "slots": cfg["slots"],
        "max_products_per_run": cfg["max_products_per_run"],
        "last_run": last_run(db) if db is not None else None,
        "next_slot": nxt,
    }


# ---------------------------------------------------------------------------
# THE SYNC
# ---------------------------------------------------------------------------


async def sync_live_products(
    db,
    *,
    trigger: str,
    actor: str,
    slot: str = MANUAL_SLOT,
    limit: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Re-push every product that is already on Shopify AND dirty. Products
    only. Never publishes a gid-less product (counted, not pushed). Runs the
    engine behind the same three gates (DARK => SIMULATED, no network), writes
    the per-product audit rows with ``actor`` as user_id, and persists ONE
    run-summary doc in ``online_sync_runs``.

    ``limit`` defaults to the shopify.live_sync.max_products_per_run setting.
    Returns the run summary, or None when a scheduled ``slot`` was already
    claimed (the run-once lock). Idempotent: a LIVE push clears
    ``locally_modified`` in the engine's write-back, so a re-run after a clean
    run selects nothing."""
    if limit is None:
        limit = live_sync_config()["max_products_per_run"]
    run = _open_run(db, trigger=trigger, actor=actor, slot=slot)
    if run is None:
        logger.info("[LIVE-SYNC] slot %s already claimed -- skipping", slot)
        return None

    dirty, taken_down_skipped = select_dirty_products(db)
    live = [d for d in dirty if (d.get("ecom") or {}).get("shopify_product_id")]
    awaiting_first_publish = len(dirty) - len(live)
    mode = shopify_push.push_mode_status(db)["mode"]

    batch = await push_product_docs(
        db, live, current_user={"user_id": actor}, max_results=limit
    )
    results = batch["results"]
    pushed_ok = sum(1 for r in results if r.get("ok"))
    refused_no_photo = sum(1 for r in results if r.get("reason") == "no_photo")
    publish_withheld = sum(1 for r in results if r.get("reason") == "publish_withheld")
    failed = sum(
        1
        for r in results
        if not r.get("ok") and r.get("reason") not in ("no_photo", "publish_withheld")
    )
    by_id = {(d.get("id") or d.get("product_id")): d for d in live}
    failures = []
    for r in results:
        if r.get("ok"):
            continue
        doc = by_id.get(r.get("target_id"), {})
        failures.append(
            {
                "product_id": r.get("target_id"),
                "sku": doc.get("sku"),
                "name": doc.get("name") or doc.get("title"),
                "code": r.get("code"),
                "reason": r.get("reason"),
                "error": r.get("error"),
            }
        )

    summary = {
        "finished_at": _utc_now(),
        "status": "done",
        "mode": mode,
        "selected": len(live),
        "attempted": len(results),
        "pushed_ok": pushed_ok,
        "failed": failed,
        "refused_no_photo": refused_no_photo,
        "publish_withheld": publish_withheld,
        "awaiting_first_publish": awaiting_first_publish,
        "taken_down_skipped": taken_down_skipped,
        "blocked_skipped": batch["blocked_skipped"],
        "limit": limit,
        "limit_reached": batch["limit_reached"],
        "failures": failures,
    }
    try:
        db[RUNS_COLLECTION].update_one({"run_id": run["run_id"]}, {"$set": summary})
    except Exception as e:  # noqa: BLE001 -- the pushes already happened; log, never raise
        logger.warning("[LIVE-SYNC] run summary write failed: %s", e)
    run.update(summary)
    logger.info(
        "[LIVE-SYNC] %s run (%s) mode=%s selected=%s ok=%s failed=%s "
        "refused=%s withheld=%s awaiting_first_publish=%s",
        trigger, slot, mode, len(live), pushed_ok, failed,
        refused_no_photo, publish_withheld, awaiting_first_publish,
    )
    return _serialise_run(run)


async def scheduled_tick(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """What the scheduler calls every POLL_MINUTES. Reads the settings, asks
    whether a slot is due, claims it and runs once. Disabled, no slot due, no
    DB, or slot already claimed -> None. Never raises."""
    try:
        cfg = live_sync_config()
        if not cfg["enabled"]:
            return None
        slot = due_slot(cfg["slots"], now)
        if slot is None:
            return None
        db = connected_db()
        if db is None:
            logger.warning("[LIVE-SYNC] slot %s due but no DB connected -- skipped", slot)
            return None
        return await sync_live_products(
            db,
            trigger="scheduled",
            actor=SCHEDULER_ACTOR,
            slot=slot,
            limit=cfg["max_products_per_run"],
        )
    except Exception as e:  # noqa: BLE001 -- a scheduler job must never kill the loop
        logger.error("[LIVE-SYNC] scheduled run failed: %s", e, exc_info=True)
        return None
