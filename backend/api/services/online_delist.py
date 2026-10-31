"""Delist-on-retire: the ONE rule that pulls a product OFF the storefront when
IMS stops selling it, and re-queues it when it comes back.

THE GAP (sync audit gap #2, owner-ordered 2026-09-06, code-traced): soft-
deleting a product (DELETE /catalog/products/{id}) or setting is_active=false
(the catalog drawer PUT, the spine PUT /products/{id}, PUT /products/master/
{id}) wrote the IMS row and did NOTHING on Shopify -- the product stayed ON
SALE forever. The 07-29 catalogue purge had to be cleaned on Shopify by hand
for exactly this reason. A take-down door already existed (POST /online-store/
push/product/{id}/take-down -> shopify_push.push_product_delist) but nothing
on the retire paths asked it.

ONE RULE, NO SECOND DOOR. Every retire path calls `delist_if_live` /
`on_active_flip` here; the Shopify write itself is the EXISTING
push_product_delist (Shopify status -> DRAFT, gid KEPT, never a delete -- the
10,355-image loss lesson). It obeys the same three dark gates: SIMULATED when
dark (nothing on Shopify, the intent still audited), LIVE only behind them.

FAIL-SOFT, like the rest of the push layer: the IMS write that called us MUST
stand even when Shopify says no. A failed take-down is recorded with a stable
code (DELIST_FAILED) on the audit row AND on the twin (ecom.online_state), so
the Catalog screen's Online column can say "still live on Shopify -- take-down
failed" instead of lying.

ORDER: the hooks run AFTER the IMS write. The soft delete keeps the row and
its gid (there is no hard delete of catalog_products anywhere in the
backend), and the catalog doors $set the WHOLE doc they loaded -- a take-down
that ran first would have its own ecom write-back (status DRAFT,
taken_down_at) clobbered by that stale copy.

REACTIVATION: is_active back to true queues the twin (ecom.locally_modified)
and lifts the take-down marker, so the next press / scheduled live sync puts
it back. No automatic republish here -- and only for a product that already
has a gid: a never-pushed product is not queued here (its FIRST publish goes
out from the queue, by a press or -- owner ruling 2026-10-20 -- the scheduled
sync, behind the product gate).
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Optional

from . import shopify_push
from .shopify_live_sync import write_push_audit
from .catalog_ecom import queue_marks

logger = logging.getLogger(__name__)

STATE_DELISTED = "DELISTED"
STATE_DELIST_FAILED = "DELIST_FAILED"
# The stable failure code on the audit row + twin; the Online column key.
CODE_DELIST_FAILED = "DELIST_FAILED"
# Every twin key this module stamps; a successful publish clears them all
# (shopify_push.writeback), as does a reactivation.
# ``delist_mode`` is the push mode the DELISTED stamp came from: only a LIVE
# take-down proves the twin is off Shopify (a DARK delist is a SIMULATED no-op
# with zero network), and the stock writer's claim read
# (online_catalog.skus_claiming_inventory_items) reads exactly that.
DELIST_KEYS = ("online_state", "delisted_at", "delist_reason", "delist_error", "delist_mode")


def _raw_db(db):
    """Accept the dependencies.get_db() connection wrapper OR a raw db.

    The wrapper (DatabaseConnection / the guarded connection / a test
    double) carries ``db`` as a REAL attribute: a class property or an
    instance attribute. A raw pymongo Database synthesises EVERY attribute
    name as a collection (Database.__getattr__), so ``hasattr(db,
    "is_connected")`` is True there, ``db.db`` is the collection named "db",
    and a Collection's truth test RAISES (pymongo 4). The catalog doors pass
    the raw db (catalog._get_db()), so the old hasattr check made every
    drawer deactivate / Delete take-down raise into delist_if_live's
    fail-soft except -- nothing ever reached Shopify from those doors. Only a
    static lookup (no __getattr__, no descriptor call) tells the two apart."""
    if db is None:
        return None
    if inspect.getattr_static(db, "db", None) is None:
        return db  # a raw database: nothing to unwrap
    if not getattr(db, "is_connected", True):
        return None
    inner = getattr(db, "db", None)
    return inner if inner is not None else db


def _resolve_twin(db, product: Dict[str, Any], *, strict: bool = False) -> Optional[Dict[str, Any]]:
    """The catalog_products twin (the doc that carries ecom.shopify_product_id).

    A doc with an `ecom` sub-doc IS a catalog doc. A spine row is keyed to its
    twin by pim_product_id (the create door's own link), then product_id / id
    (legacy convergence twins share the spine id), then sku -- the same order
    online_catalog.stamp_online_state uses. Sequential find_one (no $or) so the
    in-memory MockCollection resolves identically. Fail-soft -> None; with
    `strict` a read error RAISES (product_master.has_listing: an unread twin
    is never "not on Shopify")."""
    if "ecom" in product:
        return product
    if db is None:
        if strict:
            raise LookupError("no catalogue to read the twin from")
        return None
    try:
        coll = db["catalog_products"]
        for key in (
            product.get("pim_product_id"),
            product.get("product_id"),
            product.get("id"),
        ):
            if key:
                doc = coll.find_one({"id": key})
                if doc is not None:
                    return doc
        if product.get("sku"):
            return coll.find_one({"sku": product["sku"]})
    except Exception:  # noqa: BLE001
        if strict:
            raise
        logger.warning("[DELIST] twin lookup failed", exc_info=True)
    return None


def _stamp(db, twin_id: Optional[str], **fields: Any) -> None:
    """Read-merge-write of the ecom sub-doc (the _writeback_product idiom: works
    on MockCollection, keeps the sibling ecom keys). None deletes a key.
    Fail-soft."""
    if db is None or not twin_id:
        return
    from .catalog_ecom import update_ecom

    def _apply(ecom: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in fields.items():
            if value is None:
                ecom.pop(key, None)
            else:
                ecom[key] = value
        return ecom

    try:
        # Compare-and-set (SF-04): never drops a sibling written meanwhile.
        update_ecom(db, twin_id, _apply)
    except Exception:  # noqa: BLE001
        logger.warning("[DELIST] ecom stamp failed for %s", twin_id, exc_info=True)


async def delist_if_live(
    db,
    product: Dict[str, Any],
    *,
    reason: str,
    actor: Dict[str, Any],
    record_dark: bool = True,
) -> Optional[Dict[str, Any]]:
    """Take `product` OFF Shopify if it is there. `reason` is what IMS did
    (deleted / deactivated) and rides on the audit row as `trigger`; `actor` is
    the current_user dict. Returns the push result dict, or None when the
    product was never pushed (no gid -> nothing to do, nothing recorded).
    `record_dark=False` stamps the twin only for a LIVE result, like the
    Online Store > Take down door: a SIMULATED take-down is audited and
    nothing else, so the listing still reads as live and is taken down for
    real once the gates go live (the Never-sell-online sweep). NEVER raises."""
    try:
        db = _raw_db(db)
        twin = _resolve_twin(db, product or {})
        gid = ((twin or {}).get("ecom") or {}).get("shopify_product_id")
        if shopify_push.is_variant_of(twin or {}):
            # A size variant owns no listing: take ITS Shopify variant off
            # sale (DENY + 0) on the parent's listing, never the parent's
            # status. is_active is the marker; the quantity rule lists an
            # inactive spine at 0 so every later stock pass agrees.
            result = await shopify_push._delist_variant_row(db, twin)
            if result.action == "noop":
                return None  # never mapped -> nothing on Shopify, nothing recorded
        elif not gid:
            return None
        else:
            result = await shopify_push.push_product_delist(db, twin)
        data = result.to_dict()
        data["trigger"] = reason
        twin_id = twin.get("id") or twin.get("product_id")
        if not record_dark and data.get("mode") != shopify_push.MODE_LIVE:
            pass  # audited below; the twin keeps reading as live
        elif data.get("ok"):
            _stamp(
                db,
                twin_id,
                online_state=STATE_DELISTED,
                delisted_at=shopify_push._now(),
                delist_reason=reason,
                delist_error=None,
                delist_mode=data.get("mode"),
            )
        else:
            data["code"] = data.get("code") or CODE_DELIST_FAILED
            _stamp(
                db,
                twin_id,
                online_state=STATE_DELIST_FAILED,
                delisted_at=None,
                delist_reason=reason,
                delist_error=data.get("error") or CODE_DELIST_FAILED,
                delist_mode=data.get("mode"),
            )
        write_push_audit(data, actor)
        return data
    except Exception:  # noqa: BLE001 -- the IMS write that called us stands
        logger.warning("[DELIST] take-down hook failed (IMS write stands)", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Never sell online (owner ruling 2026-10-22): a brand the SUPERADMIN marked
# Never sell online has its live listings TAKEN DOWN -- by the brand save and,
# for anything that save left (a failed or timed-out take-down, gates that were
# dark), by every scheduled sync. Both callers use the two functions below.
# ---------------------------------------------------------------------------
REASON_NEVER_SELL = "brand_never_sell_online"


def never_sell_live_listings(db, brand: Optional[str] = None) -> List[Dict[str, Any]]:
    """{id, sku, brand} of every listing ON the website (the collection
    registry's live predicate -- a LIVE take-down drops a row out of it) whose
    brand is marked Never sell online, matched with the gate's own key rule
    (catalog_dictionary.blocked_brand_row). `brand` narrows it to that one
    brand. Dirty or not. RAISES on a read failure: callers decide."""
    from .catalog_dictionary import BRAND_COLLECTION, blocked_brand_row, brand_name_key
    from .shopify_push.collection_registry import listing_facts

    db = _raw_db(db)
    if db is None:
        return []
    blocked = [d for d in db[BRAND_COLLECTION].find({}) if d.get("never_sell_online") is True]
    if not blocked:
        return []
    probe = brand_name_key(brand) if brand is not None else None
    return [
        {"id": f.get("id"), "sku": str(f.get("sku") or ""), "brand": f.get("brand")}
        for f in listing_facts(db)
        if blocked_brand_row(blocked, f.get("brand")) is not None
        and (probe is None or brand_name_key(f.get("brand")) == probe)
    ]


async def take_down_listings(
    db, listings: List[Dict[str, Any]], *, actor: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Each listing through delist_if_live (the one take-down door; dark ->
    SIMULATED, nothing stamped). One {sku, ok, mode, error} row per listing.
    Never raises (delist_if_live does not)."""
    out: List[Dict[str, Any]] = []
    for f in listings:
        res = await delist_if_live(
            db, {"id": f.get("id"), "sku": f.get("sku")},
            reason=REASON_NEVER_SELL, actor=actor, record_dark=False,
        ) or {}
        ok = res.get("ok") is True
        out.append({
            "sku": f.get("sku"),
            "ok": ok,
            "mode": res.get("mode"),
            "error": None if ok else (res.get("error") or "take-down failed"),
        })
    return out


def mark_for_republish(db, product: Dict[str, Any]) -> bool:
    """Reactivation: queue the twin for the next press / scheduled live sync
    and lift the take-down marker (shopify_live_sync.select_dirty_products
    skips ecom.taken_down_at -- a human asking for the product back IS the
    explicit request that marker waits for). Only for a product with a gid.
    No network. Returns True when a twin was queued. Never raises."""
    try:
        db = _raw_db(db)
        twin = _resolve_twin(db, product or {})
        if shopify_push.is_variant_of(twin or {}):
            # A size variant is never queued as a listing (nothing to
            # publish); only the take-down marks are lifted. Its stock comes
            # back through the parent's next stock pass: is_active on ->
            # pooled units vs the 0 the delist recorded -> re-sent.
            _stamp(
                db,
                twin.get("id") or twin.get("product_id"),
                taken_down_at=None,
                **{k: None for k in DELIST_KEYS},
            )
            # Switched back on -- or on for the FIRST time, finishing a colour
            # that waits to join its model's listing (G2, review of #1233):
            # the model's listing is queued.
            from . import product_master as _pm

            _pm.queue_model_listing_of(db, twin)
            return True
        if not ((twin or {}).get("ecom") or {}).get("shopify_product_id"):
            return False
        # Never an archived one (queue_marks, Website 2).
        marks = queue_marks(twin)
        _stamp(
            db,
            twin.get("id") or twin.get("product_id"),
            **marks,
            taken_down_at=None,
            **{k: None for k in DELIST_KEYS},
        )
        return bool(marks)
    except Exception:  # noqa: BLE001
        logger.warning("[DELIST] republish mark failed", exc_info=True)
        return False


async def on_active_flip(
    db,
    product: Dict[str, Any],
    *,
    was_active: Any,
    now_active: Any,
    actor: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """The is_active transition, shared by every door that writes the flag.
    active -> inactive: take it down (reason "deactivated"). inactive -> active:
    queue it for republish. Same value: nothing. A MISSING is_active reads True
    (the purchasable rule)."""
    was = True if was_active is None else bool(was_active)
    now = True if now_active is None else bool(now_active)
    if was and not now:
        return await delist_if_live(db, product, reason="deactivated", actor=actor)
    if now and not was:
        mark_for_republish(db, product)
    return None
