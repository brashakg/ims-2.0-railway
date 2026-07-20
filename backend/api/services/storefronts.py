"""
IMS 2.0 - Storefronts registry  (WizOpt multi-storefront Phase 0 -- DARK)
========================================================================
A tiny registry that names every ONLINE storefront IMS can publish to. Phase 0
seeds exactly ONE row -- "Better Vision Online" (BV) -- which is the storefront
IMS already pushes to today. WizOpt (and any later storefront) is added ONLY in
a later phase; nothing here exposes a second storefront.

WHY THIS EXISTS:
IMS is (as of the Shopify unification) the SOLE writer for the Better Vision
Shopify store. To add a SECOND storefront (WizOpt) later WITHOUT forking the
push engine, every storefront-scoped decision (which credentials, which store
fulfils, which Shopify object id to write back) must be keyed on a stable
`storefront_id`. This registry is that key's home. It is additive and inert:
the row is never auto-written to prod and BV keeps behaving byte-identically.

ROW SHAPE (the BV seed sets the first block; the rest are modelled now, set
later so the schema is stable from day one):
  storefront_id      "BV"                    -- stable key (never the Mongo _id)
  name               "Better Vision Online"
  is_default         True                    -- the default storefront
  status             "ACTIVE"
  brand              "BETTER_VISION"
  -- reserved for later phases (unset now) --
  shop_domain        None   -- e.g. "bettervision.in" / "*.myshopify.com"
  entity_id          None   -- billing entity this storefront sells under
  online_store_id    None   -- the ONLINE store (BV-ONLINE-01) that fulfils
  fulfillment_policy None   -- pooled / single-store / reserve-on-order, etc.
  membership_default None   -- default product include/exclude for this front

CONTRACT: fail-soft (a read never raises), the ensure is IDEMPOTENT (re-running
never clobbers an existing row -- it only inserts when absent), and it is
DRY-RUN-CAPABLE (dry_run=True returns the plan and writes NOTHING). Nothing in
this module runs at import; nothing writes to prod unless a caller explicitly
invokes ensure with dry_run=False.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COLLECTION = "storefronts"

# The default (and, in Phase 0, only) storefront key. Shared everywhere a
# storefront-scoped lookup must fall back to "the BV storefront".
DEFAULT_STOREFRONT_ID = "BV"

# The Better Vision Online seed. The reserved keys are present but None so the
# document shape is stable from the first insert (later phases fill them in).
BV_STOREFRONT: Dict[str, Any] = {
    "storefront_id": "BV",
    "name": "Better Vision Online",
    "is_default": True,
    "status": "ACTIVE",
    "brand": "BETTER_VISION",
    # Reserved for later phases -- modelled now, set later.
    "shop_domain": None,
    "entity_id": None,
    "online_store_id": None,
    "fulfillment_policy": None,
    "membership_default": None,
}


def _get_db():
    """The app Mongo handle, or None. Fail-soft (matches the router helper)."""
    try:
        from database.connection import get_db

        return get_db().db
    except Exception:  # noqa: BLE001 -- a DB-handle read must never raise here
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_row(seed: Dict[str, Any]) -> Dict[str, Any]:
    """A full insert document from a seed def (adds created/updated stamps)."""
    now = _now()
    row = dict(seed)
    row.setdefault("created_at", now)
    row.setdefault("updated_at", now)
    return row


def get_storefront(
    db=None, storefront_id: str = DEFAULT_STOREFRONT_ID
) -> Optional[Dict[str, Any]]:
    """The registry row for one storefront, or None. Fail-soft."""
    db = db if db is not None else _get_db()
    if db is None:
        return None
    try:
        return db.get_collection(COLLECTION).find_one(
            {"storefront_id": storefront_id}, {"_id": 0}
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[STOREFRONTS] get %s failed: %s", storefront_id, e)
        return None


def list_storefronts(db=None) -> List[Dict[str, Any]]:
    """Every registered storefront (default first). Fail-soft -> []."""
    db = db if db is not None else _get_db()
    if db is None:
        return []
    try:
        rows = list(db.get_collection(COLLECTION).find({}, {"_id": 0}))
    except Exception as e:  # noqa: BLE001
        logger.debug("[STOREFRONTS] list failed: %s", e)
        return []
    rows.sort(key=lambda r: (not r.get("is_default"), str(r.get("storefront_id"))))
    return rows


def ensure_default_storefront(db=None, dry_run: bool = False) -> Dict[str, Any]:
    """Idempotently ensure the single BV storefront registry row EXISTS.

    Behaviour:
      * dry_run=True  -> inspect only. Returns the plan ("would_create" or
        "exists") and writes NOTHING. This is the safe default posture for a
        live system -- callers opt in to writing.
      * dry_run=False -> insert the BV row ONLY if it is absent (upsert with
        $setOnInsert), so an existing row (possibly already carrying
        shop_domain / online_store_id set by a later phase) is NEVER clobbered.

    Returns a structured result: {"ok", "action", "storefront_id", "dry_run"}.
    Never raises (fail-soft -> {"ok": False, "error": ...}).
    """
    db = db if db is not None else _get_db()
    result: Dict[str, Any] = {
        "ok": True,
        "storefront_id": BV_STOREFRONT["storefront_id"],
        "dry_run": bool(dry_run),
        "action": "noop",
    }
    if db is None:
        result.update(ok=False, action="skipped", error="no database handle")
        return result

    try:
        coll = db.get_collection(COLLECTION)
        existing = coll.find_one(
            {"storefront_id": BV_STOREFRONT["storefront_id"]}, {"_id": 0}
        )
        if existing:
            result["action"] = "exists"
            return result
        if dry_run:
            result["action"] = "would_create"
            result["plan"] = _seed_row(BV_STOREFRONT)
            return result
        coll.update_one(
            {"storefront_id": BV_STOREFRONT["storefront_id"]},
            {"$setOnInsert": _seed_row(BV_STOREFRONT)},
            upsert=True,
        )
        result["action"] = "created"
        logger.info(
            "[STOREFRONTS] ensured default storefront %s",
            BV_STOREFRONT["storefront_id"],
        )
        return result
    except Exception as e:  # noqa: BLE001 -- fail-soft: never break a caller
        logger.warning("[STOREFRONTS] ensure failed: %s", e)
        result.update(ok=False, action="error", error=str(e))
        return result
