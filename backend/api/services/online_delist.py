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
has a gid: a never-pushed product's FIRST publish stays a human press.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from . import shopify_push
from .shopify_live_sync import write_push_audit

logger = logging.getLogger(__name__)

STATE_DELISTED = "DELISTED"
STATE_DELIST_FAILED = "DELIST_FAILED"
# The stable failure code on the audit row + twin; the Online column key.
CODE_DELIST_FAILED = "DELIST_FAILED"
# Every twin key this module stamps; a successful publish clears them all
# (shopify_push.writeback), as does a reactivation.
DELIST_KEYS = ("online_state", "delisted_at", "delist_reason", "delist_error")


def _raw_db(db):
    """Accept the dependencies.get_db() connection wrapper OR a raw db."""
    if db is None:
        return None
    if hasattr(db, "is_connected"):
        if not db.is_connected:
            return None
        inner = getattr(db, "db", None)
        if inner is not None:
            return inner
    return db


def _resolve_twin(db, product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The catalog_products twin (the doc that carries ecom.shopify_product_id).

    A doc with an `ecom` sub-doc IS a catalog doc. A spine row is keyed to its
    twin by pim_product_id (the create door's own link), then product_id / id
    (legacy convergence twins share the spine id), then sku -- the same order
    online_catalog.stamp_online_state uses. Sequential find_one (no $or) so the
    in-memory MockCollection resolves identically. Fail-soft -> None."""
    if "ecom" in product:
        return product
    if db is None:
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
        logger.warning("[DELIST] twin lookup failed", exc_info=True)
    return None


def _stamp(db, twin_id: Optional[str], **fields: Any) -> None:
    """Read-merge-write of the ecom sub-doc (the _writeback_product idiom: works
    on MockCollection, keeps the sibling ecom keys). None deletes a key.
    Fail-soft."""
    if db is None or not twin_id:
        return
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": twin_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        for key, value in fields.items():
            if value is None:
                ecom.pop(key, None)
            else:
                ecom[key] = value
        coll.update_one({"id": twin_id}, {"$set": {"ecom": ecom}})
    except Exception:  # noqa: BLE001
        logger.warning("[DELIST] ecom stamp failed for %s", twin_id, exc_info=True)


async def delist_if_live(
    db, product: Dict[str, Any], *, reason: str, actor: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Take `product` OFF Shopify if it is there. `reason` is what IMS did
    (deleted / deactivated) and rides on the audit row as `trigger`; `actor` is
    the current_user dict. Returns the push result dict, or None when the
    product was never pushed (no gid -> nothing to do, nothing recorded).
    NEVER raises."""
    try:
        db = _raw_db(db)
        twin = _resolve_twin(db, product or {})
        gid = ((twin or {}).get("ecom") or {}).get("shopify_product_id")
        if not gid:
            return None
        result = await shopify_push.push_product_delist(db, twin)
        data = result.to_dict()
        data["trigger"] = reason
        twin_id = twin.get("id") or twin.get("product_id")
        if data.get("ok"):
            _stamp(
                db,
                twin_id,
                online_state=STATE_DELISTED,
                delisted_at=shopify_push._now(),
                delist_reason=reason,
                delist_error=None,
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
            )
        write_push_audit(data, actor)
        return data
    except Exception:  # noqa: BLE001 -- the IMS write that called us stands
        logger.warning("[DELIST] take-down hook failed (IMS write stands)", exc_info=True)
        return None


def mark_for_republish(db, product: Dict[str, Any]) -> bool:
    """Reactivation: queue the twin for the next press / scheduled live sync
    and lift the take-down marker (shopify_live_sync.select_dirty_products
    skips ecom.taken_down_at -- a human asking for the product back IS the
    explicit request that marker waits for). Only for a product with a gid.
    No network. Returns True when a twin was queued. Never raises."""
    try:
        db = _raw_db(db)
        twin = _resolve_twin(db, product or {})
        if not ((twin or {}).get("ecom") or {}).get("shopify_product_id"):
            return False
        _stamp(
            db,
            twin.get("id") or twin.get("product_id"),
            locally_modified=True,
            taken_down_at=None,
            **{k: None for k in DELIST_KEYS},
        )
        return True
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
