"""
IMS 2.0 - Online Store : Shopify PUSH Router  (BVI Phase 5 -- IMS -> Shopify)
============================================================================
The IMS -> Shopify PUSH control surface. It drives the GraphQL push engine
(api/services/shopify_push.py) for the four ecom entities and reports the current
push posture.

***** BUILT DARK (the non-negotiable safety contract) *****
Every push here is SIMULATED -- it returns a dry-run PLAN and makes NO Shopify
network call -- UNLESS ALL of: IMS_SHOPIFY_WRITES on AND DISPATCH_MODE=live AND
Shopify creds present in the `integrations` collection. Default / missing-creds /
gate-off => SIMULATED. Per #262 BVI is the single Shopify writer; the IMS push
stays retired until the owner flips the gates in the Phase-6 baton cutover. See
docs/reference/BVI_MERGE_PLAN.md A.3 + Phase 5.

ROLE GATE (router-level): SUPERADMIN / ADMIN ONLY. Pushing to the live storefront
is integration-critical, so -- unlike the rest of the Online Store module (which
also admits CATALOG_MANAGER / DESIGN_MANAGER) -- the push surface is narrowed to
SUPERADMIN + ADMIN. SUPERADMIN is auto-granted by require_roles. Every route is
catalogued in api/services/rbac_policy.POLICY with exactly {ADMIN, SUPERADMIN}
(kept in lock-step -- test_rbac_policy.test_no_uncatalogued_routes is the lock).

AUDIT EVERYTHING: every push ATTEMPT writes a chained audit_logs row
(get_audit_repository().create) capturing the mode (SIMULATED|LIVE), the target,
and the structured result -- so the owner has an immutable record of every push,
dry-run or live (SYSTEM_INTENT: Audit Everything). Audit is fail-soft: an audit
error never undoes / blocks the push.

Mounted at /api/v1/online-store/push:
  POST /product/{product_id}      push a catalog product (+ ecom + variants)
  POST /collection/{collection_id} push an ecom_collections doc (+ smart ruleSet)
  POST /menu/{menu_id}            push an ecom_menus doc (the nav / mega-menu)
  POST /image/{image_id}          push ONE APPROVED product image (productCreateMedia)
  POST /stock                     write the pooled quantity of every changed listing
  GET  /status                    per-entity pushed-vs-pending + the current mode

Everything is FAIL-SOFT: no DB -> writes 503 (not a false 200); reads degrade to
zeros; a Shopify error becomes a structured {ok:false} result, never a 500.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_roles
from ..services import shopify_push
from ..services import shopify_live_sync as live_sync
# The DB / audit / doc helpers and the product-sweep core live in the
# live-sync service so the manual sweep and the scheduled sync run ONE code
# path; re-imported by their old names so nothing else here moved.
from ..services.shopify_live_sync import (  # noqa: F401
    all_docs as _all_docs,
    connected_db as _get_db,
    variants_for_product as _get_variants_for_product,
    write_push_audit as _write_audit,
)

router = APIRouter()

# Push is integration-critical -> SUPERADMIN / ADMIN ONLY (narrower than the rest
# of the Online Store module). SUPERADMIN is auto-granted by require_roles, so it
# is not repeated in the tuple but IS listed in every POLICY row.
_PUSH_ROLES = ("ADMIN",)


# ---------------------------------------------------------------------------
# THE BATCH CAP (owner ruling 2026-08-25 -- "one press, goes live")
# ---------------------------------------------------------------------------
# Pressing publish now puts products in front of customers immediately, so ONE
# wrong press must affect a BOUNDED number of products, not the whole catalogue.
# 25 is the sane default: small enough that a mistake is 25 listings to take
# down one by one (the /product/{id}/take-down route below), large enough that
# the current ~59-item catalogue clears in three presses.
#
# This is a HARD SERVER-SIDE cap on the PRODUCT sweep, not a default the caller
# can raise -- the frontend passes limit=100 explicitly, so a default nobody
# reads would cap nothing. It deliberately does NOT clamp the other entities:
# collections / menus / images / the variant-prices resync do not put a new
# listing in front of a customer, and the resync PAGES through the whole mapped
# set (OS-017) -- clamping it would just make that loop run out of pages.
#
# A capped sweep reports limit_reached=True, so the existing "run again to
# continue" cue fires: a press that stopped early must never read as complete.
PRODUCT_BATCH_CAP = 25


def _queue_order(doc: Dict) -> tuple:
    """Where one dirty product sits in the press queue: NEVER-PUSHED rows first,
    then the longest-waiting.

    The cap above is a hard 25 and a withheld publish BURNS a slot (it really
    did reach Shopify) *and* is re-queued -- so in natural collection order 25
    permanently-stuck rows (a broken image host, an unpriced tranche) sit at the
    front of EVERY press forever and the photographed, priced products behind
    them never go live, however often the owner presses. That is the "run again
    to continue" lie one layer down: the sentence promises progress a repeat
    press cannot make.

    `ecom.last_pushed_at` is stamped by _writeback_product on every row that
    reached Shopify, and the re-queue preserves it (read-merge-write of the
    whole sub-doc), so ordering by it drifts a stuck row to the BACK after its
    first attempt -- the cap keeps meaning 25 real write attempts and the queue
    drains. Stringified so a legacy ISO-string stamp and a datetime can never
    raise on comparison.
    """
    stamp = (doc.get("ecom") or {}).get("last_pushed_at")
    return (0, "") if not stamp else (1, str(stamp))


# ---------------------------------------------------------------------------
# DB helpers (fail-soft; mirror routers/online_store_collections.py)
# ---------------------------------------------------------------------------


def _require_db():
    db = _get_db()
    if db is None:
        # No DB -> the push store is unavailable. 503 (not a false 200).
        raise HTTPException(
            status_code=503, detail="Online Store push unavailable (no DB)"
        )
    return db


# ---------------------------------------------------------------------------
# Push routes -- one per entity. Each runs the engine + writes a chained audit
# row + returns the structured PushResult.
# ---------------------------------------------------------------------------


@router.post("/product/{product_id}")
async def push_product(
    product_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Push a catalog product (+ its ecom sub-doc + catalog_variants) to Shopify.

    DARK by default -> SIMULATED dry-run (the ProductInput plan, no network call).
    LIVE only behind the three gates. Writes a chained audit row either way. An
    unknown product is a 404; a product with no `ecom` sub-doc is a 400 (it was
    never staged for the online store)."""
    db = _require_db()
    product = _get_catalog_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if not product.get("ecom"):
        raise HTTPException(
            status_code=400,
            detail="Product has no ecom sub-doc -- stage it for the online store first",
        )
    variants = _get_variants_for_product(db, product)
    result = await shopify_push.push_product(db, product, variants)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.post("/product/{product_id}/take-down")
async def take_down_product(
    product_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Pull ONE product OFF the live storefront (Shopify status -> DRAFT).

    THE REVERSIBILITY THAT MAKES ONE-PRESS PUBLISHING SURVIVABLE (owner ruling
    2026-08-25). Pressing publish now puts products in front of customers
    immediately, so there has to be a way to pull ONE bad listing straight back
    -- the engine could already do it (push_product_delist, built for the
    "block collection from online" cutover) but nothing could ask it to.

    NOT a delete: the Shopify product is kept and so is its id, so putting the
    product back is an ordinary push and can never mint a duplicate listing.
    The engine also records the take-down in IMS (ecom.status DRAFT, dirty flag
    cleared) so the screen agrees with the storefront and the next sweep does
    not resurrect it seconds later.

    DARK by default -> a SIMULATED plan. A product that was never on Shopify is
    a clean no-op, not an error. Unknown product -> 404; writes the same chained
    audit row as every other push."""
    db = _require_db()
    product = _get_catalog_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    result = await shopify_push.push_product_delist(db, product)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.post("/collection/{collection_id}")
async def push_collection(
    collection_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Push an ecom_collections doc to Shopify (collectionCreate/Update + smart
    ruleSet when SMART). DARK by default; LIVE behind the gates. Writes a chained
    audit row. Unknown collection -> 404."""
    db = _require_db()
    repo = _collection_repo(db)
    doc = repo.get_by_id(collection_id) if repo else None
    if doc is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    # Temporary "share as PDF" collections are internal, auto-expiring sharing
    # sets -- NEVER a storefront collection. Refuse to push one even if targeted
    # by hand (structural exclusion: they also carry sync_to_shopify=False /
    # published=False). This is the one isolated sync-selection guard; the push
    # engine (services/shopify_push.py) is intentionally not touched.
    if doc.get("is_temporary") or doc.get("sync_to_shopify") is False:
        raise HTTPException(
            status_code=400,
            detail="Temporary collections are internal sharing sets and are never pushed to Shopify",
        )
    result = await shopify_push.push_collection(db, doc)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.post("/menu/{menu_id}")
async def push_menu(
    menu_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Push an ecom_menus doc (the Online Store nav / mega-menu) to Shopify
    (menuCreate/Update, mapping the nested item tree). DARK by default; LIVE
    behind the gates. Writes a chained audit row. Unknown menu -> 404."""
    db = _require_db()
    repo = _menu_repo(db)
    doc = repo.get_by_id(menu_id) if repo else None
    if doc is None:
        raise HTTPException(status_code=404, detail="Menu not found")
    result = await shopify_push.push_menu(db, doc)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.post("/image/{image_id}")
async def push_image(
    image_id: str,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Push ONE APPROVED product image to Shopify (productCreateMedia onto its
    parent product). DARK by default; LIVE behind the gates. Writes a chained
    audit row. Unknown image -> 404. A non-APPROVED image is NOT a route error
    (the engine returns ok=false action=skip) so the audit still records the
    refusal."""
    db = _require_db()
    repo = _image_repo(db)
    doc = repo.get_by_id(image_id) if repo else None
    if doc is None:
        raise HTTPException(status_code=404, detail="Image not found")
    result = await shopify_push.push_image(db, doc)
    data = result.to_dict()
    _write_audit(data, current_user)
    return {"result": data}


@router.post("/stock")
async def push_stock(
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Write the pooled quantity of every product already on Shopify whose
    number changed since it was last sent (owner ruling 2026-09-07 -- make
    website quantities real). Products only; never publishes anything. DARK
    -> a SIMULATED plan and zero network. ONE chained audit row per run (the
    per-product outcome is in its payload). No DB -> 503."""
    db = _require_db()
    data = (await shopify_push.sync_stock_levels(db)).to_dict()
    _write_audit(data, current_user)
    return data


@router.get("/status")
async def push_status(
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Report the CURRENT push posture + per-entity pushed-vs-pending counts.

    `mode` block: are we DARK or LIVE, and WHY (the three gate components +
    creds-present). `counts` block: per entity, how many docs are already mapped
    to Shopify (pushed) vs still pending (a dirty `locally_modified` row, or one
    with no Shopify id yet). Fail-soft: no DB -> zeros + db_connected False, never
    a 500."""
    db = _get_db()
    # RESOLVED, not merely remembered: this endpoint feeds the "Online Store
    # channel" tile, and a per-process cache makes that tile red on a healthy
    # shop after every deploy (see push_mode_status_resolved).
    mode = await shopify_push.push_mode_status_resolved(db)
    if db is None:
        return {
            "mode": mode,
            "db_connected": False,
            "counts": _empty_counts(),
            "live_sync": live_sync.status_block(None),
        }

    # Counts are computed in Python over the (bounded) ecom collections rather
    # than via nested `ecom.*` / `$exists` Mongo queries. Reasons: (1) the in-memory
    # MockCollection used in no-DB / test mode does not model dot-notation or
    # `$exists`, so a server-side query would silently mis-count there; (2) this is
    # a status/dashboard endpoint (not a hot path) and these collections are small
    # (the PIM master is one row per product, collections/menus are tens of rows),
    # so a single bounded pass is cheap AND exact on BOTH backends. Fail-soft -> the
    # entity block degrades to zeros, never a 500.
    counts = {
        "products": _product_counts(db),
        "collections": _doc_counts(db, "ecom_collections", "shopify_collection_id"),
        "menus": _doc_counts(db, "ecom_menus", "shopify_menu_id"),
        "images": _image_counts(db),
    }
    return {
        "mode": mode,
        "db_connected": True,
        "counts": counts,
        # The scheduled live-product sync: settings, last run, next IST slot.
        "live_sync": live_sync.status_block(db),
    }


def _audit_collection_is_real_mongo(audit) -> bool:
    """True when the audit repo's underlying collection is a real pymongo
    Collection, False for the in-memory MockCollection used in no-DB / test
    mode. Discriminator: a real pymongo Collection exposes `.database`
    (the Database it belongs to); MockCollection carries no such attribute --
    the SAME discriminator AuditRepository.create() already relies on via
    `getattr(self.collection, "database", None)`.

    Why this matters here: MockCollection's `_matches_filter` does dict
    equality on the filter's literal keys, so a Mongo dot-notation key like
    `"details.mode"` is looked up as ONE flat key `doc.get("details.mode")` --
    which is never present on a doc that actually stores a nested `details`
    dict. It does not raise; it just silently matches nothing. Pushing the
    mode/ok filters into the query is therefore ONLY safe on a real pymongo
    collection; the Mock path keeps the pre-existing over-fetch + Python
    post-filter. Fail-soft -> False (falls back to the safe path)."""
    try:
        return getattr(getattr(audit, "collection", None), "database", None) is not None
    except Exception:  # noqa: BLE001
        return False


@router.post("/sync-live")
async def sync_live_now(
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """The human's "Sync live products now" button (owner ruling 2026-09-06).

    Runs EXACTLY what the 01:00 / 09:00 IST scheduled sync runs
    (services/shopify_live_sync.sync_live_products): every product ALREADY on
    Shopify that was edited in IMS is re-pushed through the same engine and
    the same three gates (DARK => SIMULATED, no network). A dirty product that
    was never pushed is counted as awaiting_first_publish and NOT published --
    first publish stays a human press on the product itself. Honours the
    max_products_per_run setting; one audit row per product with the caller
    as actor; one run-summary doc in online_sync_runs. No DB -> 503."""
    db = _require_db()
    run = await live_sync.sync_live_products(
        db, trigger="manual", actor=current_user.get("user_id") or "unknown"
    )
    return {"run": run, "live_sync": live_sync.status_block(db)}


@router.get("/history")
async def push_history(
    limit: int = 50,
    mode: Optional[str] = None,
    ok: Optional[bool] = None,
    entity: Optional[str] = None,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Read-only push HISTORY from the chained ONLINE_STORE_PUSH audit ledger
    (OS-047). Every push attempt -- LIVE or dry-run -- already writes an
    audit_logs row (_write_audit above); this surfaces that ledger on the sync
    page so the operator can see what was sent, when, by whom, and what failed,
    without knowing the Activity Log's internal action string. Visible to the
    SAME roles that can push (ADMIN + SUPERADMIN -- previously ADMINs had no
    history surface at all: the Activity Log page is SUPERADMIN-only).

    Filters: `mode` (LIVE|SIMULATED), `ok` (true/false), `entity` (product /
    collection / menu / image / variant-prices).

    On a REAL Mongo deployment the mode/ok filters are pushed straight into
    the query (`details.mode` / `details.ok`) so a filtered view (e.g.
    "Failures only") sees the WHOLE ledger -- previously they were applied in
    Python AFTER a flat 500-row over-fetch, so a filter went blind past
    whatever sat outside the newest 500 rows (a busy ledger could hide every
    older failure from the "Failures" view). The in-memory MockCollection
    (tests / no-DB mode) does not understand dot-notation queries, so it keeps
    the original over-fetch-then-filter-in-Python path -- harmless there since
    test fixtures are always small. The Python filter still runs afterward
    either way, as a redundant safety net.

    Sorted on `timestamp` (stamped by _write_audit on every row), which rides
    the existing `(action, timestamp)` compound index every other audit_logs
    reader already relies on (database/connection.py ensure_indexes) --
    previously sorted on the unindexed `created_at`, an full-ledger-scan sort
    that only got slower as the ledger grew.

    READ-ONLY: the ledger stays append-only; nothing here mutates audit rows.
    Fail-soft: no audit store, OR the query itself failing, both resolve to
    the SAME honest `available: false` payload -- a query error is explicitly
    NOT treated as "the ledger is genuinely empty" (that would misreport a
    real read failure as "nothing has ever been pushed")."""
    limit = max(1, min(int(limit), 200))
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
    except Exception:  # noqa: BLE001
        audit = None
    if audit is None:
        return {"entries": [], "count": 0, "available": False}

    want_mode = mode.strip().upper() if mode else None
    real_mongo = _audit_collection_is_real_mongo(audit)

    flt: Dict[str, Any] = {"action": "ONLINE_STORE_PUSH"}
    if entity:
        flt["entity_type"] = entity.strip().lower()
    if real_mongo:
        if want_mode:
            flt["details.mode"] = want_mode
        if ok is not None:
            flt["details.ok"] = bool(ok)
        fetch_n = limit
    else:
        # Over-fetch when a details filter is active (the filter is post-hoc
        # on this path -- Mock cannot evaluate the dot-notation keys above).
        fetch_n = limit if (want_mode is None and ok is None) else 500

    try:
        rows = audit.find_many(flt, sort=[("timestamp", -1)], limit=fetch_n)
    except Exception:  # noqa: BLE001
        return {"entries": [], "count": 0, "available": False}

    entries: List[Dict[str, Any]] = []
    for r in rows:
        d = r.get("details") or {}
        if want_mode and str(d.get("mode") or "").upper() != want_mode:
            continue
        if ok is not None and bool(d.get("ok")) is not ok:
            continue
        entries.append(
            {
                # `timestamp` is stamped going forward; a row written before
                # this change falls back to `created_at` so older ledger
                # entries still render a date instead of null.
                "timestamp": r.get("timestamp") or r.get("created_at"),
                "user_id": r.get("user_id"),
                "entity": r.get("entity_type"),
                "target_id": r.get("entity_id"),
                "mode": d.get("mode"),
                "push_action": d.get("push_action"),
                "ok": d.get("ok"),
                "shopify_id": d.get("shopify_id"),
                "error": d.get("error"),
                "reason": d.get("reason"),
                "code": d.get("code"),
            }
        )
        if len(entries) >= limit:
            break

    _enrich_history_entries(_get_db(), entries)
    return {"entries": entries, "count": len(entries), "available": True}


def _enrich_history_entries(db, entries: List[Dict[str, Any]]) -> None:
    """Best-effort readability enrichment for the history rows: product ids ->
    sku + name (a raw uuid is unreadable on the panel), user ids -> display
    name. Per-id find_one lookups (bounded by the <=200-row page) so it works
    identically on the MockCollection. Fail-soft: any error leaves the raw ids."""
    if db is None or not entries:
        return
    prod_cache: Dict[str, Optional[Dict]] = {}
    user_cache: Dict[str, Optional[str]] = {}
    for e in entries:
        tid = e.get("target_id")
        if tid and e.get("entity") in ("product", "variant-prices"):
            if tid not in prod_cache:
                try:
                    prod_cache[tid] = db["catalog_products"].find_one({"id": tid})
                except Exception:  # noqa: BLE001
                    prod_cache[tid] = None
            doc = prod_cache[tid]
            if doc:
                e["sku"] = doc.get("sku") or doc.get("parent_sku")
                e["name"] = doc.get("name") or doc.get("title")
        uid = e.get("user_id")
        if uid:
            if uid not in user_cache:
                try:
                    u = db["users"].find_one({"user_id": uid})
                    user_cache[uid] = (
                        (u.get("full_name") or u.get("name") or u.get("username"))
                        if u
                        else None
                    )
                except Exception:  # noqa: BLE001
                    user_cache[uid] = None
            if user_cache[uid]:
                e["user_name"] = user_cache[uid]


@router.post("/all-pending")
async def push_all_pending(
    entities: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    current_user: dict = Depends(require_roles(*_PUSH_ROLES)),
) -> Dict[str, Any]:
    """Sweep EVERY pending/dirty ecom doc and push it via the same per-entity
    engine -- the queue-drain that the Phase-6 cutover actually runs (today only
    per-entity single pushes exist). Pending = a `locally_modified` product /
    collection / menu, or an APPROVED product image with no Shopify id yet.

    `entities` is an optional CSV filter (products,collections,menus,images;
    default all). `limit` caps the total number of pushes in one sweep (a safety
    valve against a runaway batch); when the cap is hit `limit_reached` is True
    and the caller should run again to continue (OS-046 -- the FE surfaces it).
    `offset` pages the variant-prices resync ONLY (see below); it is REJECTED
    (400) whenever more than one entity is selected alongside it -- see the
    guard below for why.

    `variant-prices` is an OPT-IN extra entity (NOT in the default set): a
    normal product push already carries the variant price/barcode side channel,
    so sweeping it by default would double-push. Select it explicitly
    (?entities=variant-prices) to re-sync price/compareAtPrice/barcode for
    EVERY product already mapped to Shopify -- the "bulk MRP revision" resync.
    Its eligible set (mapped products) never shrinks after a resync, so unlike
    the locally_modified queues a bare limit would rescan the same first N docs
    forever (OS-017). It therefore pages deterministically: docs are sorted by
    catalog id, `offset` says where to start, and the response carries
    `eligible_total` + `next_offset` (null when the whole set was covered) so
    the caller can loop until done. A product whose variants have no stored
    Shopify gid returns action="noop" and is tallied under `noop`, NEVER under
    `pushed` -- a no-op must not read as a successful price update.

    DARK by default -- each push is SIMULATED (a dry-run plan, NO Shopify network
    call) unless the same three gates are open (IMS_SHOPIFY_WRITES + DISPATCH_MODE
    =live + creds). The current posture is returned in `mode` so the caller knows
    whether this sweep was a dry-run or a real cutover push. Writes one chained
    audit row per push. Fail-soft: a single doc's failure never aborts the sweep;
    no DB -> 503."""
    db = _require_db()
    selected = {
        e.strip().lower()
        for e in (
            entities.split(",")
            if entities
            else ["products", "collections", "menus", "images"]
        )
        if e.strip()
    }

    # Combined-entity offset guard (#950 follow-up OS-017-adjacent): `offset`
    # only has meaning for the variant-prices resync's OWN eligible-set walk.
    # When another entity shares the call, THAT entity's per-doc pushes consume
    # `limit` slots first (the sweep order below runs products before
    # variant-prices), so the variant-prices loop can hit its `len(results) >=
    # limit` break at `processed == 0` -- returning `next_offset == offset`.
    # A caller that naively loops "call again with next_offset until null"
    # spins forever, never advancing and never reaching the entities that DID
    # have slots. Reject the combination outright rather than silently
    # returning a call that looks like progress but makes none; a caller that
    # wants a paged variant-prices resync must run it alone
    # (?entities=variant-prices), exactly like the FE's sync-page loop already
    # does. This also sidesteps the second reported risk -- a positional
    # offset can skip a doc if the mapped set mutates between calls -- for the
    # multi-entity case, since that path no longer accepts offset>0 at all.
    if offset > 0 and len(selected) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "offset paging is only valid with a single entity selected "
                "(e.g. ?entities=variant-prices&offset=...) -- combining it "
                "with other entities can starve the paged walk of its limit "
                "budget and never advance"
            ),
        )

    mode = shopify_push.push_mode_status(db)
    results: List[Dict[str, Any]] = []
    summary: Dict[str, Dict[str, int]] = {}
    cap_reached = False
    stock: Optional[Dict[str, Any]] = None

    def _tally(entity: str, data: Dict[str, Any]) -> None:
        bucket = summary.setdefault(entity, {"pushed": 0, "failed": 0, "noop": 0})
        bucket.setdefault("noop", 0)
        if data.get("reason") == "no_photo":
            # THE PHOTO RULE (owner ruling 2026-08-25, "no photo, no publish").
            # A refusal is neither a success nor a breakage: it gets its OWN
            # line so the operator sees WHY a product did not go live and can
            # fix it (add a photograph, press again). Filing it under `failed`
            # would read as a Shopify error; folding it into `pushed` would be
            # the silent lie this whole change exists to end.
            bucket["refused_no_photo"] = bucket.get("refused_no_photo", 0) + 1
        elif data.get("reason") == "publish_withheld":
            # The product reached Shopify but is NOT visible (no publication,
            # no photograph on Shopify, no provable price). Same treatment as a
            # refusal and for the same reason: filing it under `pushed` is the
            # green-toast-over-a-no-op this whole change exists to end, and
            # filing it under `failed` would read as a Shopify breakage the
            # operator cannot act on. Its own line, with its own count.
            bucket["publish_withheld"] = bucket.get("publish_withheld", 0) + 1
        elif data.get("reason") == "archived_not_listed":
            # The write landed, but an ARCHIVED product is not on the
            # storefront. Same treatment, same reason: `pushed` must mean a
            # shopper can find it.
            bucket["archived_not_listed"] = bucket.get("archived_not_listed", 0) + 1
        elif data.get("ok") and data.get("action") == "noop":
            # A clean no-op (nothing mapped/priced to send) is NOT a success
            # push -- tallied separately so the UI renders it honestly (OS-017:
            # "N processed" must never imply an MRP revision reached Shopify
            # when nothing was sent).
            bucket["noop"] += 1
        elif data.get("ok"):
            bucket["pushed"] += 1
            if data.get("code") == shopify_push.PRICE_NOT_SYNCED:
                # Live, but the price step failed: the product IS on the
                # storefront (so it is pushed), at the OLD price (so it gets
                # its own line and stays queued -- see push_product).
                bucket["price_not_synced"] = bucket.get("price_not_synced", 0) + 1
        else:
            bucket["failed"] += 1
        results.append(data)

    # The sweep order mirrors a dependency-safe cutover: products (+ variants)
    # first, then the collections/menus that reference them, then images last.
    if "products" in selected:
        # ONE product-sweep core, shared with the scheduled live sync
        # (services/shopify_live_sync): dirty minus taken-down, the block
        # classification hoisted once per batch (findings #17/#18/#20), one
        # audit row per push. Only the ORDER and the CAPS are this door's:
        dirty_products, taken_down_skipped = live_sync.select_dirty_products(db)
        # THE QUEUE MUST DRAIN. See _queue_order: without this the same first 25
        # stuck rows are retried on every press and nothing behind them ever
        # reaches bettervision.in.
        dirty_products.sort(key=_queue_order)
        # THE BATCH CAP: at most PRODUCT_BATCH_CAP products actually go out
        # per press. A photo-less REFUSAL does not count against it -- it
        # never reached Shopify and nothing went live, and spending the whole
        # cap on refusals would publish nothing at all.
        batch = await live_sync.push_product_docs(
            db,
            dirty_products,
            current_user=current_user,
            max_results=limit,
            max_sent=PRODUCT_BATCH_CAP,
        )
        for data in batch["results"]:
            _tally("products", data)
        cap_reached = batch["cap_reached"]
        if batch["blocked_skipped"]:
            summary.setdefault("products", {"pushed": 0, "failed": 0, "noop": 0})[
                "blocked_skipped"
            ] = batch["blocked_skipped"]
        if taken_down_skipped:
            summary.setdefault("products", {"pushed": 0, "failed": 0, "noop": 0})[
                "taken_down_skipped"
            ] = taken_down_skipped
        # A press also pushes STOCK (owner ruling 2026-09-07): every listing
        # whose pooled quantity changed since it was last sent, in one pass,
        # AFTER the product pushes so a product this press just created is
        # covered too. Its OWN key (not a `results` row): `results` and
        # `pushed_count` are per-object pushes, and a stock pass is neither a
        # listing nor a slot against the batch cap. One audit row per run.
        stock = (await shopify_push.sync_stock_levels(db)).to_dict()
        _write_audit(stock, current_user)

    # OPT-IN price/barcode resync (never in the default set -- the product
    # sweep above already pushes prices as part of each product push). Targets
    # every product ALREADY mapped to Shopify; the engine skips gid-less /
    # priceless variants and no-ops cleanly (tallied under `noop`, above).
    # PAGED (OS-017): the eligible set never shrinks after a resync, so the
    # sweep sorts deterministically by catalog id and walks [offset:] --
    # repeated calls with the returned next_offset cover the WHOLE mapped set
    # instead of rescanning the same first N docs forever.
    eligible_total: Optional[int] = None
    next_offset: Optional[int] = None
    if "variant-prices" in selected:
        eligible = [
            d
            for d in _all_docs(db, "catalog_products")
            if (d.get("ecom") or {}).get("shopify_product_id")
        ]
        eligible.sort(key=lambda d: str(d.get("id") or d.get("sku") or ""))
        eligible_total = len(eligible)
        start = max(0, int(offset))
        processed = 0
        for doc in eligible[start:]:
            if len(results) >= limit:
                break
            variants = _get_variants_for_product(db, doc)
            data = (await shopify_push.push_variant_prices(db, doc, variants)).to_dict()
            _write_audit(data, current_user)
            _tally("variant-prices", data)
            processed += 1
        if start + processed < eligible_total:
            next_offset = start + processed

    if "collections" in selected:
        for doc in _all_docs(db, "ecom_collections"):
            if len(results) >= limit:
                break
            if not doc.get("locally_modified"):
                continue
            data = (await shopify_push.push_collection(db, doc)).to_dict()
            _write_audit(data, current_user)
            _tally("collections", data)

    if "menus" in selected:
        for doc in _all_docs(db, "ecom_menus"):
            if len(results) >= limit:
                break
            if not doc.get("locally_modified"):
                continue
            data = (await shopify_push.push_menu(db, doc)).to_dict()
            _write_audit(data, current_user)
            _tally("menus", data)

    if "images" in selected:
        for doc in _all_docs(db, "product_images"):
            if len(results) >= limit:
                break
            is_approved = str(doc.get("status") or "").upper() == "APPROVED"
            if not is_approved or doc.get("shopify_image_id"):
                continue
            data = (await shopify_push.push_image(db, doc)).to_dict()
            _write_audit(data, current_user)
            _tally("images", data)

    # "N processed" must mean N objects the press actually did the work on.
    # A photo-less REFUSAL never reached Shopify, a WITHHELD publish left the
    # product invisible, and an ARCHIVED row was retired on purpose -- folding
    # any of them into the processed number turns the screen into the same lie
    # as "pending: 0" over an empty queue. They are counted, and shown, on their
    # own lines in `summary`.
    not_done = sum(
        (b.get("refused_no_photo") or 0)
        + (b.get("publish_withheld") or 0)
        + (b.get("archived_not_listed") or 0)
        # A FAILED row reached nobody either -- a Shopify userError, a transport
        # failure, or a fail-closed block check that made zero calls at all.
        # Counting it as processed reads "3 processed" over zero visible, which
        # is the same lie in the same place.
        + (b.get("failed") or 0)
        for b in summary.values()
    )
    return {
        "mode": mode,
        "db_connected": True,
        "pushed_count": len(results) - not_done,
        # A sweep the cap stopped early must NEVER read as complete -- the
        # caller's "run again to continue" cue keys off this flag.
        "limit_reached": len(results) >= limit or cap_reached,
        # How many products one press may put live. Reported so the screen
        # can say the real number instead of hard-coding one.
        "batch_cap": PRODUCT_BATCH_CAP,
        # Paging block (variant-prices only; null otherwise). next_offset=null
        # means the whole eligible set was covered -- the caller may stop.
        "offset": offset,
        "next_offset": next_offset,
        "eligible_total": eligible_total,
        "summary": summary,
        # The stock pass that rode this press (products selected only); None
        # when no products were swept. {mode, action, ok, payload{changed,
        # synced, failed, location_id, ...}, code, error}.
        "stock": stock,
        "results": results,
    }


def _product_counts(db) -> Dict[str, int]:
    """staged = catalog_products carrying an `ecom` sub-doc; pushed = those whose
    ecom has a shopify_product_id; pending = those whose ecom is dirty
    (locally_modified). Computed in Python (portable + exact)."""
    staged = pushed = pending = 0
    for doc in _all_docs(db, "catalog_products"):
        ecom = doc.get("ecom")
        if not ecom:
            continue
        staged += 1
        if ecom.get("shopify_product_id"):
            pushed += 1
        if ecom.get("locally_modified"):
            pending += 1
    return {"staged": staged, "pushed": pushed, "pending": pending}


def _doc_counts(db, name: str, shopify_field: str) -> Dict[str, int]:
    """total / pushed (has the shopify id) / pending (locally_modified) for a
    flat ecom collection (ecom_collections, ecom_menus)."""
    total = pushed = pending = 0
    for doc in _all_docs(db, name):
        total += 1
        if doc.get(shopify_field):
            pushed += 1
        if doc.get("locally_modified"):
            pending += 1
    return {"total": total, "pushed": pushed, "pending": pending}


def _image_counts(db) -> Dict[str, int]:
    """approved (push-eligible) / pushed (has shopify_image_id) / pending (APPROVED
    but not yet pushed)."""
    approved = pushed = pending = 0
    for doc in _all_docs(db, "product_images"):
        is_approved = str(doc.get("status") or "").upper() == "APPROVED"
        has_gid = bool(doc.get("shopify_image_id"))
        if is_approved:
            approved += 1
            if not has_gid:
                pending += 1
        if has_gid:
            pushed += 1
    return {"approved": approved, "pushed": pushed, "pending": pending}


def _empty_counts() -> Dict[str, Any]:
    return {
        "products": {"staged": 0, "pushed": 0, "pending": 0},
        "collections": {"total": 0, "pushed": 0, "pending": 0},
        "menus": {"total": 0, "pushed": 0, "pending": 0},
        "images": {"approved": 0, "pushed": 0, "pending": 0},
    }


# ---------------------------------------------------------------------------
# Doc fetch helpers (reuse the Phase 1-4 repositories / catalog access)
# ---------------------------------------------------------------------------


def _get_catalog_product(db, product_id: str) -> Optional[Dict]:
    """Fetch a catalog_products doc by its `id` (the catalog key, never _id).

    Defensive fallback (audit OS-004): when the id misses, retry by `sku` and
    then `parent_sku`. Door-created spine mirrors key their catalog doc on
    pim_product_id (a DIFFERENT uuid from the spine product_id) but always
    carry the spine sku as `parent_sku`; BVI-imported docs carry `sku`. This
    keeps a caller holding a sku (or a legacy id) from getting a misleading
    404 "Product not found". Sequential find_one calls (no $or) so the
    in-memory MockCollection used by tests resolves identically.
    Strips the Mongo _id. Fail-soft -> None."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None and product_id:
            doc = coll.find_one({"sku": product_id})
        if doc is None and product_id:
            doc = coll.find_one({"parent_sku": product_id})
        if doc is not None:
            doc.pop("_id", None)
        return doc
    except Exception:  # noqa: BLE001
        return None


def _collection_repo(db):
    try:
        from database.repositories import EcomCollectionRepository

        return EcomCollectionRepository(db["ecom_collections"])
    except Exception:  # noqa: BLE001
        return None


def _menu_repo(db):
    try:
        from database.repositories import EcomMenuRepository

        return EcomMenuRepository(db["ecom_menus"])
    except Exception:  # noqa: BLE001
        return None


def _image_repo(db):
    try:
        from database.repositories import ProductImageRepository

        return ProductImageRepository(db["product_images"])
    except Exception:  # noqa: BLE001
        return None
