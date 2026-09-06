"""Shopify push -- shared

Imports, logger, push-mode constants, PushResult and the three
push gates (writes enabled / dispatch mode / creds) -- the single source of
truth for DARK vs LIVE. Every other sub-module imports from here.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import logging
import os
import random

import httpx

# Reuse the existing Shopify safety primitives -- do NOT fork the writer.
from agents.nexus_providers import (
    ims_shopify_writes_enabled,
    shopify_dispatch_mode,
    _as_shopify_gid,
    SHOPIFY_API_VERSION,
)

# Credential resolution is centralised in shopify_auth: it prefers OAuth
# client-credentials (mint-and-cache) over the stale static Mongo token that
# 401s on the Admin API. Both _has_shopify_creds (the gate) and _graphql (the
# network boundary) source shop_url + access_token from here.
from api.services.shopify_auth import resolve_shopify_credentials

# Attribute -> Shopify filter-tag generator (BVI parity). Pure, network-free.
from ..gtin import sanitise_gtin
from ..ecom_category_map import ims_to_shopify_type
from ..shopify_tag_gen import generate_attribute_tags, merge_tag_lists

# The publication name + the per-process publication-id cache live with the
# GraphQL documents; the gate reader below shares that one dict object.
from .queries import _ONLINE_STORE_PUBLICATION_NAME, _publication_id_cache

# __package__ (not __name__): every sub-module shares the ONE logger the
# flat module had, named "api.services.shopify_push".
logger = logging.getLogger(__package__)

PROVIDER_TIMEOUT = float(os.getenv("NEXUS_PROVIDER_TIMEOUT", "30.0"))

# Push modes returned in every PushResult.mode.
MODE_SIMULATED = "SIMULATED"
MODE_LIVE = "LIVE"
MODE_BLOCKED = "BLOCKED"  # Hub Phase 5: push refused -- brand/collection push-locked


@dataclass
class PushResult:
    """Structured result of one push attempt. Returned by every push_* function
    and recorded verbatim on the chained audit row by the router.

    mode         SIMULATED (dry-run, no network) | LIVE (a real Shopify write).
    entity       product | variant | variant-prices | collection | menu | image.
    action       create | update | skip | noop (what we did / would do).
    target_id    the IMS doc id we were asked to push.
    ok           True unless an error occurred (a SIMULATED dry-run is ok=True).
    shopify_id   the Shopify gid (set on a LIVE write OR echoed if already mapped).
    payload      the dry-run plan (SIMULATED) or the mutation variables (LIVE).
    error        a human string when ok=False; None otherwise.
    reason       why we are SIMULATED (gate-off / dispatch / no-creds) -- advisory.
    """

    mode: str
    entity: str
    action: str
    target_id: Optional[str] = None
    ok: bool = True
    shopify_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    reason: Optional[str] = None
    # Stable machine code for a failure the operator can act on (e.g.
    # PUBLISH_SCOPE_MISSING). `error` stays the plain-language line; the raw
    # vendor text lives on the side-channel dict (`publication.error`).
    code: Optional[str] = None
    # Product pushes only: the attribute->metafield side channel. SIMULATED ->
    # the planned rows; LIVE -> {"set": n, "errors": [...]}. None elsewhere.
    metafields: Optional[Any] = None
    # Product pushes only: the variant price/barcode side channel (owner
    # priority: "change MRP in IMS -> website updates"). SIMULATED -> the
    # planned ProductVariantsBulkInput rows; LIVE -> a summary dict. None
    # elsewhere (and None when the product has no variants).
    variant_prices: Optional[Any] = None
    # Product CREATE only (and UPDATE when SHOPIFY_PUSH_PRICE_ON_UPDATE is on):
    # the variant-seeding side channel that gives the new Shopify variants their
    # price / compareAtPrice / barcode / SKU and captures their gids. SIMULATED
    # -> the planned rows; LIVE -> an {updated, created, skipped, errors,
    # variant_gids, inventory_item_gids, ...} summary (the inventory-item gids
    # are the oversell-guard stock targets persisted for the resolver). None
    # elsewhere.
    variants_seeded: Optional[Any] = None
    # Product pushes only: the Online-Store sales-channel publish side channel
    # ({published, publication_id} or {published: False, error}). Runs on every
    # product push (create AND update). None when the push never reached the
    # publish step (dry-run, refusal, ARCHIVED).
    publication: Optional[Any] = None
    # Collection pushes only (CUSTOM): the manual-membership side channel (the
    # collectionAddProducts step -- IMS's stored manual member list reproduced on
    # Shopify). SIMULATED -> the planned {product_ids, skipped_not_on_shopify};
    # LIVE -> an {added, skipped_not_on_shopify, errors} summary. None for SMART
    # (Shopify derives SMART membership from the ruleSet) and non-collection pushes.
    membership: Optional[Any] = None
    # Product pushes only: the photograph side channel. SIMULATED -> the
    # media diff plan {attach, delete, reorder, unmanaged, hands_off}; LIVE ->
    # {attached, deleted, reordered, unmanaged, on_shopify, hands_off, error?,
    # code?} from media.sync_product_media. None when the push never got that
    # far (refusal, transport error).
    photos: Optional[Any] = None
    # Product pushes only: the STOCK side channel (owner ruling 2026-09-07 --
    # make website quantities real). SIMULATED -> the plan {policy, quantities,
    # location}; LIVE -> {ok, tracked, set, quantities, location_id, errors}.
    # None when the push never reached it (refusal, ARCHIVED, transport error).
    stock: Optional[Any] = None
    # Product pushes only: the TAG side channel (sync audit gap #4 -- IMS
    # manages only the tags it sent; hand-added Shopify tags survive).
    # SIMULATED -> the plan {add, remove, unmanaged, adopt, create}; LIVE ->
    # {added, removed, unmanaged, adopted, error?, code?} from
    # tags.sync_product_tags. None when the push never got that far.
    tags: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# The product is LIVE but the price step of the SAME press failed (sync audit
# gap #7). ok stays True -- the product IS on the storefront, and a false
# "failed" would read as a Shopify breakage over a listing that exists -- but
# the row STAYS QUEUED and the result carries this code so the operator sees
# the website is selling at the OLD price.
PRICE_NOT_SYNCED = "PRICE_NOT_SYNCED"
_PRICE_NOT_SYNCED_MSG = (
    "Live on the website at the OLD price: the price change did not reach "
    "Shopify. The product stays queued -- press again, or the next scheduled "
    "sync retries it."
)


# ===========================================================================
# Gating -- the single source of truth for "are we DARK or LIVE?"
# ===========================================================================


def _env_on(name: str) -> bool:
    """A truthy env flag, read at CALL time (never cached at import) so a
    deploy-time change -- or a test monkeypatch -- takes effect immediately.
    Mirrors nexus_providers.ims_shopify_writes_enabled's accepted values."""
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "on", "yes")


def price_on_update_enabled() -> bool:
    """SHOPIFY_PUSH_PRICE_ON_UPDATE -- default OFF.

    OFF (default): an UPDATE of an already-mapped product behaves EXACTLY as it
    did before the variant-seeding fix -- no price/sku seeding, no variant-gid
    capture. This is deliberate: ~4,400 products are already live on Shopify and
    silently re-pricing them from IMS is an owner decision, not a side effect of
    a catalogue edit. (The existing, separately gated variant-price push is
    unaffected: it still updates variants that ALREADY carry a stored gid.)"""
    return _env_on("SHOPIFY_PUSH_PRICE_ON_UPDATE")


def push_mode_status(db) -> Dict[str, Any]:
    """Report the CURRENT push posture without pushing anything (drives GET
    /online-store/push/status). Pure read; never raises.

    Returns the three gate components + the derived effective mode so the owner
    (and the UI banner) can see exactly why pushes are DARK or LIVE:
      writes_enabled  -- IMS_SHOPIFY_WRITES on?
      dispatch_mode   -- off / test / live
      creds_present   -- shop_url + access_token in the integrations collection?
      mode            -- LIVE only when all three align, else SIMULATED.

    Plus THE THIRD DOOR, which the three gates say nothing about: the Online
    Store publication. An ACTIVE product published to no sales channel is
    invisible, so a press with no resolvable publication now honestly WITHHOLDS
    -- and the owner should be able to see that BEFORE he presses, not discover
    it one product at a time afterwards. Reported without a network call: the
    pinned SHOPIFY_ONLINE_STORE_PUBLICATION_ID, else whatever a previous
    lookup cached this process, else unresolved. (This replaces the deleted
    publish_on_create flag -- publishing is no longer optional, so the honest
    signal is "can we publish at all?".)
    """
    writes = ims_shopify_writes_enabled()
    disp = shopify_dispatch_mode()
    creds = _has_shopify_creds(db)
    live = bool(writes and disp == "live" and creds)
    pinned = (os.getenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID") or "").strip()
    pub_id = pinned or _publication_id_cache.get(_ONLINE_STORE_PUBLICATION_NAME)
    # THE FOURTH DOOR: the Shopify location the online quantity is written at.
    # Read without a network call (pinned env / the persisted registry row);
    # push_mode_status_resolved asks Shopify once when LIVE and unknown.
    from .inventory import stored_online_location_id  # lazy: inventory imports here

    loc_id, loc_source = stored_online_location_id(db)
    return {
        "online_location_id": loc_id,
        "online_location_source": loc_source or "unresolved",
        "publishes_to_online_store": True,
        "online_store_publication_id": pub_id or None,
        "online_store_publication_source": (
            "pinned" if pinned else ("looked_up" if pub_id else "unresolved")
        ),
        "mode": MODE_LIVE if live else MODE_SIMULATED,
        "writes_enabled": writes,
        "dispatch_mode": disp,
        "creds_present": creds,
        "is_live": live,
        "api_version": SHOPIFY_API_VERSION,
        # Additive, default-OFF behaviour flags (see the module docstring).
        "price_on_update": price_on_update_enabled(),
        "single_writer_note": (
            "IMS is the single Shopify writer (BVI was retired on 2026-07-20). "
            "Push runs LIVE only when IMS_SHOPIFY_WRITES=1 AND "
            "SHOPIFY_DISPATCH_MODE=live (or global DISPATCH_MODE=live) AND "
            "Shopify credentials are configured; otherwise it is SIMULATED."
        ),
    }



def _has_shopify_creds(db, storefront_id: str = "BV") -> bool:
    """True iff usable Shopify Admin API credentials resolve (shop_url +
    access_token), via OAuth client-credentials OR the vault/env fallback --
    NOT a raw read of the (possibly stale) stored token. So the gate now reports
    creds-present whenever OAuth env creds are configured, even if the Mongo
    vault token is a known-bad placeholder. Fail-soft -> False (treated as DARK).

    `storefront_id` (default "BV") keys the resolver; the BV default is
    byte-identical to the previous single-arg call.

    NOTE: this is only ever reached (in _live_or_reason) AFTER the writes +
    dispatch gates pass, so in the DARK default posture no OAuth token is minted.
    push_mode_status calls it directly; with the in-process token cache that
    mints at most ~once per TTL."""
    try:
        creds = resolve_shopify_credentials(db, storefront_id)
        return bool(creds and creds.get("shop_url") and creds.get("access_token"))
    except Exception:  # noqa: BLE001 -- a config read must never raise into a push
        return False


def _live_or_reason(db) -> Tuple[bool, Optional[str]]:
    """Decide LIVE vs SIMULATED and, when SIMULATED, WHY. The three gates are
    checked in a fixed order so the reason is deterministic + actionable."""
    if not ims_shopify_writes_enabled():
        return (
            False,
            "writes_disabled (IMS_SHOPIFY_WRITES off -- BVI is the single writer)",
        )
    if shopify_dispatch_mode() != "live":
        return (
            False,
            f"shopify_dispatch_mode={shopify_dispatch_mode()} (need live; set "
            "SHOPIFY_DISPATCH_MODE=live or global DISPATCH_MODE=live)",
        )
    if not _has_shopify_creds(db):
        return False, "shopify creds not configured (shop_url/access_token)"
    return True, None


def push_lock_reason(db, entity: str, doc: Dict[str, Any]) -> Optional[str]:
    """Hub Phase 5 (owner DECISION C): return a reason if this entity is push-
    LOCKED, else None. A locked brand (product) or collection handle in the
    `ecom.shopify_push_locks` E2 config may NEVER be pushed -- this is checked as
    the FIRST statement inside every push fn, BEFORE the dark/live gate, so a lock
    is absolute (fail-closed). Matching is case-insensitive. Fail-SOFT on a config-
    read error -> None (a read blip must not block every push; the normal gate
    still applies)."""
    try:
        from ..policy_engine import get_policy

        locks = get_policy("ecom.shopify_push_locks", default={}) or {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(locks, dict):
        return None

    def _norm(v: Any) -> str:
        return str(v or "").strip().lower()

    if entity == "product":
        attrs = doc.get("attributes") or {}
        brand = _norm(doc.get("brand") or doc.get("vendor") or attrs.get("brand_name"))
        if brand and brand in {_norm(b) for b in (locks.get("brands") or [])}:
            return "brand '%s' is push-locked" % brand
    elif entity == "collection":
        handle = _norm(doc.get("handle") or doc.get("title"))
        if handle and handle in {_norm(c) for c in (locks.get("collections") or [])}:
            return "collection '%s' is push-locked" % handle
    return None


def _blocked_result(entity: str, target_id: Optional[str], reason: str) -> "PushResult":
    """A fail-closed push refusal (brand/collection push-locked)."""
    return PushResult(
        mode=MODE_BLOCKED,
        entity=entity,
        action="skip",
        target_id=target_id,
        ok=False,
        error="push-locked: " + reason,
        reason=reason,
    )

