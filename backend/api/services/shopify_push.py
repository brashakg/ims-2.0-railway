"""
IMS 2.0 - Shopify GraphQL push engine  (BVI Phase 5 -- IMS -> Shopify PUSH)
==========================================================================
The IMS -> Shopify PUSH side of the "Online Store" module: take the IMS ecom
models (catalog_products + ecom sub-doc, catalog_variants, ecom_collections,
ecom_menus, APPROVED product_images) and push them to the Shopify storefront
(bettervision.in) via the Shopify GraphQL Admin API.

Full target architecture + the single-writer invariant: docs/reference/BVI_MERGE_PLAN.md
section A.3 + Phase 5.

***** THIS IS BUILT DARK (the non-negotiable safety contract) *****
Every push is SIMULATED -- it returns a dry-run PLAN and makes NO network call --
UNLESS ALL THREE hold:
  1. ims_shopify_writes_enabled()  -- IMS_SHOPIFY_WRITES is on (default OFF). Per
     #262 BVI is the SINGLE Shopify writer; the IMS push stays retired until the
     owner flips this gate as part of the Phase-6 baton cutover.
  2. shopify_dispatch_mode() == "live" -- SHOPIFY_DISPATCH_MODE when set (owner
     2026-07-05: lets Shopify go live WITHOUT arming the global DISPATCH_MODE,
     which would also arm WhatsApp/SMS), else the global DISPATCH_MODE.
  3. Shopify creds present         -- resolvable shop_url + access_token via
     shopify_auth.resolve_shopify_credentials(db): OAuth client-credentials
     (minted from SHOPIFY_CLIENT_ID/SECRET) preferred, else the Mongo vault or
     env static token.
Default / missing-creds / gate-off  ->  mode="SIMULATED", no Shopify call.

We REUSE the existing, code-verified safety primitives rather than reinvent them:
  - nexus_providers.ims_shopify_writes_enabled()  (the single-writer kill-switch)
  - shopify_auth.resolve_shopify_credentials(db)  (OAuth-preferred creds resolver)
  - nexus_providers.shopify_dispatch_mode() / _as_shopify_gid()  (live gate + GID helper)

IDEMPOTENT: on a LIVE push the Shopify gid returned by the mutation is written
BACK onto the IMS doc (ecom.shopify_product_id / shopify_variant_id /
shopify_collection_id / shopify_menu_id / shopify_image_id), keyed on the IMS
join key (never Mongo _id), so a re-push UPDATES the same Shopify object instead
of creating a duplicate. The presence of a stored Shopify id is what selects
create-vs-update in the mutation.

VARIANT SEEDING ON CREATE (2026-07 fix -- IMS is the sole Shopify writer):
ProductInput carries NO price and NO sku (the 2024-04+ product model moved both
onto the variant), so a bare productCreate lands a product whose default variant
is price 0.00 with no SKU. Every CREATE therefore runs a second step:
productCreate returns its auto-created variant(s); we set price /
compareAtPrice / barcode / inventoryItem.sku on them via
productVariantsBulkUpdate, create any REMAINING IMS variants via
productVariantsBulkCreate, and write every returned ProductVariant gid back
(ecom.shopify_variant_id + catalog_variants.shopify_variant_id) so a later price
push can find them. UPDATES of already-mapped products are UNCHANGED by default
-- seeding a price onto the ~4,400 live products is opt-in via
SHOPIFY_PUSH_PRICE_ON_UPDATE=1.

INVENTORY-ITEM CAPTURE (oversell-guard publish precondition, stacks on the
seeding fix): the same returned variants also select inventoryItem { id }, and
that gid is persisted ALONGSIDE the variant gid --
catalog_variants.shopify_inventory_item_id per variant row, plus
ecom.shopify_inventory_item_id for a product with NO catalog_variants rows
(its single "Default Title" variant IS the product). Those are exactly the two
fields the stock write-back resolver reads
(online_catalog.online_variant_targets_for_skus / inventory_items_for_skus,
online_sync_health._inventory_item_id_for_sku): without them a product IMS
creates on Shopify can never have its listed quantity synced down after an
in-store sale -- unguardable against oversell. shopify_location_id is NOT
captured here: the create/update response carries no location (this push never
sets stock), and the resolver sources the location from
SHOPIFY_ONLINE_LOCATION_ID / the integrations config.

Optional env flags (all default OFF -- nothing changes unless the owner sets them):
  SHOPIFY_PUSH_PRICE_ON_UPDATE=1  also seed price/sku + capture variant gids on
                                  an UPDATE of an already-mapped product.

Sales-channel publish is NOT optional and has no flag (owner ruling 2026-08-25,
"one press, goes live"): every product push publishes to the Online Store
channel, because an ACTIVE product published to no channel is invisible on the
storefront. Publish is still WITHHELD unless the price is provably right and the
product has a photograph ON SHOPIFY. The publication id can be pinned with
SHOPIFY_ONLINE_STORE_PUBLICATION_ID (else it is looked up once via `publications`,
which needs the read_publications scope).

FAIL-SOFT: every function returns a structured PushResult and NEVER raises. A
Shopify/GraphQL error becomes {ok: False, error: ...}; a missing doc becomes a
404-style {ok: False}. A push must never take down the caller.

The single network boundary is `_graphql()`. It is the ONLY thing that talks to
Shopify; tests monkeypatch it so no real Shopify call ever happens in a default
or test code path (belt-and-suspenders on top of the gate, which already blocks
the live branch by default).
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
from .gtin import sanitise_gtin
from .shopify_tag_gen import generate_attribute_tags, merge_tag_lists

logger = logging.getLogger(__name__)

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
    # Product pushes only: the photograph side channel ({attached: n} or
    # {attached: 0, error}). Set when THIS press attached the product's photos
    # to Shopify (productCreateMedia); None when the Shopify product already
    # carried media, or the push never got that far.
    photos: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
    return {
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
        from .policy_engine import get_policy

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


# ===========================================================================
# The single Shopify network boundary -- monkeypatched in tests
# ===========================================================================


# Bounded retry for Shopify throttling (HTTP 429 / GraphQL THROTTLED) and
# transient faults (5xx, timeouts). The Phase-6 queue-drain of ~4,400 products
# WILL hit the Shopify rate limiter; without a retry every throttled push
# becomes a spurious ok=False. _MAX_RETRIES is TOTAL attempts (1 original +
# up to 3 retries), base 1s doubling + jitter, Retry-After honored when sent.
# 4xx user errors are NEVER retried (they are deterministic failures).
_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 1.0  # seconds; doubles per attempt
_RETRY_MAX_DELAY = 30.0  # cap, also applied to a vendor Retry-After


def _is_throttled_body(body: Any) -> bool:
    """True when a transport-200 GraphQL body carries a top-level THROTTLED
    error (Shopify's cost-based rate limiter). Fail-soft -> False."""
    try:
        for e in (body or {}).get("errors") or []:
            if (
                isinstance(e, dict)
                and (e.get("extensions") or {}).get("code") == "THROTTLED"
            ):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _retry_delay(attempt: int, retry_after: Optional[str]) -> float:
    """Backoff before retry N (attempt is 1-based): honor a vendor Retry-After
    header when present, else exponential base-1s doubling plus jitter."""
    if retry_after:
        try:
            ra = float(retry_after)
            if ra > 0:
                return min(ra, _RETRY_MAX_DELAY)
        except (TypeError, ValueError):
            pass
    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5)
    return min(delay, _RETRY_MAX_DELAY)


async def _post_once(
    url: str, headers: Dict[str, str], payload: Dict[str, Any]
) -> httpx.Response:
    """One raw HTTP POST to Shopify. Split out of _graphql as the retry seam --
    tests monkeypatch THIS to simulate 429/THROTTLED/5xx sequences while the
    retry loop above it stays real."""
    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
        return await client.post(url, headers=headers, json=payload)


async def _graphql(db, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """POST one GraphQL operation to the Shopify Admin API and return the parsed
    JSON body. This is the ONLY function that performs a Shopify network call --
    it is reached ONLY on the LIVE branch (all three gates passed). Tests
    monkeypatch this so no real call is ever made.

    RESILIENT: retries up to _MAX_RETRIES total attempts on 429 / GraphQL
    THROTTLED / 5xx / timeout with exponential backoff (+ Retry-After when
    present). Non-retryable 4xx raises immediately.

    Returns the raw GraphQL response dict ({"data": ...} and/or {"errors": ...}).
    Raises httpx/ValueError on a transport-level failure; the caller catches and
    converts to a fail-soft PushResult.
    """
    # Keyed to the default BV storefront (Phase 0); byte-identical to the
    # previous single-arg resolve for BV.
    creds = resolve_shopify_credentials(db, "BV")
    shop_url = (creds or {}).get("shop_url")
    access_token = (creds or {}).get("access_token")
    if not shop_url or not access_token:
        # Should never happen (gate checked creds) but guard anyway.
        raise ValueError("shopify creds missing at GraphQL call time")
    url = f"https://{shop_url}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "content-type": "application/json",
    }
    payload = {"query": query, "variables": variables}

    last_error = "unknown"
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = await _post_once(url, headers, payload)
        except httpx.TimeoutException as e:
            last_error = f"timeout: {e}"
            if attempt >= _MAX_RETRIES:
                raise ValueError(
                    f"shopify request failed after {attempt} attempts ({last_error})"
                )
            logger.warning(
                "[SHOPIFY_PUSH] timeout on attempt %d/%d; retrying",
                attempt,
                _MAX_RETRIES,
            )
            await asyncio.sleep(_retry_delay(attempt, None))
            continue

        status = resp.status_code
        if status in (200, 201):
            body = resp.json() or {}
            if _is_throttled_body(body):
                last_error = "graphql THROTTLED"
                if attempt >= _MAX_RETRIES:
                    # Give the caller the real body: _user_errors turns the
                    # top-level errors into a fail-soft ok=False result.
                    return body
                logger.warning(
                    "[SHOPIFY_PUSH] THROTTLED on attempt %d/%d; retrying",
                    attempt,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(
                    _retry_delay(attempt, resp.headers.get("Retry-After"))
                )
                continue
            return body

        if status == 429 or status >= 500:
            last_error = f"status {status}: {resp.text[:200]}"
            if attempt >= _MAX_RETRIES:
                raise ValueError(
                    f"shopify request failed after {attempt} attempts ({last_error})"
                )
            logger.warning(
                "[SHOPIFY_PUSH] retryable status %d on attempt %d/%d; retrying",
                status,
                attempt,
                _MAX_RETRIES,
            )
            await asyncio.sleep(_retry_delay(attempt, resp.headers.get("Retry-After")))
            continue

        # A non-retryable 4xx (bad token, bad payload...) fails immediately --
        # replaying a deterministic user error only burns the rate budget.
        raise ValueError(f"status {status}: {resp.text[:200]}")

    raise ValueError(f"shopify request failed ({last_error})")  # unreachable guard


def _user_errors(body: Dict[str, Any], mutation_field: str) -> Optional[str]:
    """Extract a Shopify error string from a GraphQL response, or None if clean.

    A transport-200 can still carry top-level `errors` OR per-field `userErrors`;
    both are failures. We look at the named mutation field's userErrors plus any
    top-level errors so nothing is silently swallowed (Fail Loudly)."""
    if not isinstance(body, dict):
        return "malformed graphql response"
    if body.get("errors"):
        return f"graphql errors: {str(body['errors'])[:300]}"
    data = body.get("data") or {}
    field_obj = data.get(mutation_field) or {}
    ue = field_obj.get("userErrors") or []
    if ue:
        return f"userErrors: {str(ue)[:300]}"
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# GraphQL operations (the Phase-5 push set; BVI_MERGE_PLAN.md A.3)
# ===========================================================================
# Pinned, minimal mutations. We keep them small + explicit so a Shopify default
# bump can't silently change the contract. Each create returns the new gid which
# we write back for idempotency; each update is selected when a gid already exists.

# NOTE (API 2024-10 product model): ProductInput carries NO price and NO sku --
# both live on the VARIANT since 2024-04. productCreate therefore auto-creates a
# single variant (Shopify: "Only one product variant is created and linked with
# the first option value specified for each option name") at price 0.00 with no
# SKU. We select that variant back so the seeding step can price + SKU it; the
# extra selection is read-only and changes nothing about what is written.
#
# inventoryItem { id } rides on the same selection (oversell-guard publish
# precondition): the InventoryItem gid is what the stock write-back resolver
# needs (catalog_variants.shopify_inventory_item_id) to sync the listed
# quantity down after an in-store sale. Read-only; no extra network call.
_PRODUCT_CREATE = """
mutation imsProductCreate($input: ProductInput!) {
  productCreate(input: $input) {
    product {
      id
      handle
      variants(first: 100) {
        nodes { id title selectedOptions { name value } inventoryItem { id } }
      }
      media(first: 1) { nodes { id } }
    }
    userErrors { field message }
  }
}
"""

# The UPDATE mutation ALSO selects the variants back, but the seeding step is
# only reached on an update when SHOPIFY_PUSH_PRICE_ON_UPDATE is on (default
# OFF) -- so by default an update is byte-identical to before apart from this
# read-only selection.
_PRODUCT_UPDATE = """
mutation imsProductUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product {
      id
      handle
      variants(first: 100) {
        nodes { id title selectedOptions { name value } inventoryItem { id } }
      }
      media(first: 1) { nodes { id } }
    }
    userErrors { field message }
  }
}
"""

_COLLECTION_CREATE = """
mutation imsCollectionCreate($input: CollectionInput!) {
  collectionCreate(input: $input) {
    collection { id handle }
    userErrors { field message }
  }
}
"""

_COLLECTION_UPDATE = """
mutation imsCollectionUpdate($input: CollectionInput!) {
  collectionUpdate(input: $input) {
    collection { id handle }
    userErrors { field message }
  }
}
"""

# CUSTOM-collection MANUAL membership push (parity with BVI's
# ecommerce/src/lib/shopify.ts addProductsToCollection). CollectionInput does
# NOT carry a manual product list, so a CUSTOM collection's members are attached
# in a SEPARATE step after the collection upsert. Idempotent: re-adding an
# existing member is a no-op on Shopify. SMART collections never use this (their
# membership is derived by Shopify from the ruleSet).
_COLLECTION_ADD_PRODUCTS = """
mutation imsCollectionAddProducts($id: ID!, $productIds: [ID!]!) {
  collectionAddProducts(id: $id, productIds: $productIds) {
    collection { id }
    userErrors { field message }
  }
}
"""
# Shopify accepts many ids per call; chunk to stay well within limits.
_COLLECTION_PRODUCTS_PER_CALL = 250

_MENU_CREATE = """
mutation imsMenuCreate($title: String!, $handle: String!, $items: [MenuItemCreateInput!]!) {
  menuCreate(title: $title, handle: $handle, items: $items) {
    menu { id handle }
    userErrors { field message }
  }
}
"""

_MENU_UPDATE = """
mutation imsMenuUpdate($id: ID!, $title: String!, $handle: String!, $items: [MenuItemUpdateInput!]!) {
  menuUpdate(id: $id, title: $title, handle: $handle, items: $items) {
    menu { id handle }
    userErrors { field message }
  }
}
"""

_PRODUCT_CREATE_MEDIA = """
mutation imsProductCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media { ... on MediaImage { id } }
    mediaUserErrors { field message }
  }
}
"""

# Variant price/barcode push (owner priority: "change MRP in IMS -> website
# updates"). Shopify retired productVariantUpdate; the current path is
# productVariantsBulkUpdate keyed on the PARENT product gid (mirrors BVI's
# ecommerce/src/lib/shopify.ts updateVariantPrice). `barcode` is a top-level
# ProductVariantsBulkInput field in our pinned API version.
_VARIANTS_BULK_UPDATE = """
mutation imsVariantPricesUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price compareAtPrice barcode }
    userErrors { field message }
  }
}
"""

# Shopify caps productVariantsBulkUpdate at 250 variants per call (eyewear
# products carry a handful, but the cap keeps a pathological doc safe).
_VARIANTS_PER_CALL = 250

# CREATE-side companion: productCreate only ever materialises ONE variant, so
# any REMAINING IMS variant (a second colour / size) has to be created. Same
# ProductVariantsBulkInput shape, plus optionValues to place it on the option
# grid. Returns the new gids (and each variant's inventoryItem gid -- the
# oversell-guard stock target) so they can be written back for idempotency.
_VARIANTS_BULK_CREATE = """
mutation imsVariantsBulkCreate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkCreate(productId: $productId, variants: $variants) {
    productVariants { id title selectedOptions { name value } inventoryItem { id } }
    userErrors { field message }
  }
}
"""

# Sales-channel publish. A product that
# is ACTIVE but published to NO channel is invisible on the storefront; this is
# the step that puts it on the Online Store. Only `userErrors` is selected so
# the operation stays valid across Admin API versions.
_PUBLISHABLE_PUBLISH = """
mutation imsPublishablePublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    userErrors { field message }
  }
}
"""

# Publication lookup (only when SHOPIFY_ONLINE_STORE_PUBLICATION_ID is not set).
# Needs the read_publications scope; fail-soft when the app lacks it.
_PUBLICATIONS_QUERY = """
query imsPublications {
  publications(first: 25) { nodes { id name } }
}
"""

# The Shopify sales channel whose publication we target on create.
_ONLINE_STORE_PUBLICATION_NAME = "Online Store"
# Resolved once per process (a publication id is stable for the shop).
_publication_id_cache: Dict[str, str] = {}


# ===========================================================================
# Payload builders -- map IMS ecom docs -> Shopify GraphQL input. Pure; testable.
# ===========================================================================


def build_product_input(
    product: Dict[str, Any], variants: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build a Shopify ProductInput from a catalog_products doc (+ its ecom
    sub-doc) and its catalog_variants. The Shopify gid (if already mapped) is set
    so the SAME object is updated rather than duplicated."""
    ecom = product.get("ecom") or {}
    seo = ecom.get("seo") or {}
    title = (
        product.get("title")
        or product.get("name")
        or product.get("model")
        or product.get("sku")
        or "Untitled product"
    )
    # OWNER RULING 2026-08-25 -- "one press, goes live": pressing push IS the act
    # of putting the product in front of customers, so the payload goes out
    # ACTIVE. This used to map ecom.status through {DRAFT->DRAFT, PUBLISHED->
    # ACTIVE, ARCHIVED->ARCHIVED} defaulting to DRAFT -- and since every product
    # is BORN ecom.status=DRAFT and nothing in IMS ever advanced it, every push
    # ever made sent a DRAFT. The press reported "sent, failed 0" and the brand
    # page stayed empty. ARCHIVED is the one status still honoured: it is a
    # deliberate retirement, not an un-advanced create-time default.
    inp: Dict[str, Any] = {
        "title": title,
        "status": (
            "ARCHIVED"
            if str(ecom.get("status") or "").upper() == "ARCHIVED"
            else "ACTIVE"
        ),
    }
    sid = ecom.get("shopify_product_id")
    if sid:
        inp["id"] = _as_shopify_gid(sid, "Product")
    if ecom.get("handle"):
        inp["handle"] = ecom["handle"]
    if product.get("brand"):
        inp["vendor"] = product["brand"]
    if product.get("category"):
        inp["productType"] = str(product["category"])
    body_html = seo.get("html") or product.get("description")
    if body_html:
        inp["descriptionHtml"] = body_html
    if ecom.get("theme_suffix"):
        inp["templateSuffix"] = ecom["theme_suffix"]
    if seo.get("title") or seo.get("description"):
        inp["seo"] = {
            "title": seo.get("title") or title,
            "description": seo.get("description") or "",
        }
    # Tags = union of the product's manual/browse tags (ecom.seo.tags) + the
    # attribute-derived `<prefix>_<value>` filter tags the BVI admin app
    # auto-generates (shopify_tag_gen). Reproducing BVI's tokens is what keeps a
    # LIVE productUpdate (which REPLACES the whole tags array) from wiping the
    # storefront's filter tags. Pure + deterministic; no new network.
    attrs = product.get("attributes") or {}
    extras: Dict[str, Any] = {}
    if product.get("brand"):
        # Brand lives top-level on the product doc; feed it so the brand_ tag is
        # emitted even when `attributes` has no brand_name.
        extras["brand_name"] = product["brand"]
    generated_tags = generate_attribute_tags(product.get("category"), attrs, extras)
    merged_tags = merge_tag_lists(seo.get("tags") or [], generated_tags)
    if merged_tags:
        inp["tags"] = merged_tags
    # Variant identity is carried as options/skus only (price/qty stay BVI/stock
    # owned -- online qty is the derived allocation, not pushed from here).
    if variants:
        inp["productOptions"] = _derive_options(variants)
    return inp


# Owner 2026-07-05 ("CREATE WITH METAFIELDS"): category attributes (frame
# material, temple length, UV protection, ...) push to Shopify as STRUCTURED
# metafields under the `ims` namespace -- not only baked into the description.
# Storefront filtering then only needs the owner to add metafield definitions
# in Shopify admin (Search & Discovery); the data is already on every product.
_METAFIELD_NAMESPACE = "ims"
_METAFIELDS_PER_CALL = 25  # Shopify metafieldsSet hard cap per mutation
_MAX_METAFIELDS = 50  # sanity cap per product (attributes are short lists)

_METAFIELDS_SET = """
mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id key }
    userErrors { field message }
  }
}
"""


def build_product_metafields(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map the product's `attributes` dict (the canonical home of the
    category-specific fields) onto Shopify MetafieldsSetInput rows (without
    ownerId -- the pusher stamps that once the product gid is known).

    Pure + deterministic: scalar attributes only (dict/list/None/blank
    skipped), keys lowercased snake_case truncated to Shopify's 30-char key
    limit, values stringified, sorted by key, capped at _MAX_METAFIELDS."""
    attrs = product.get("attributes") or {}
    if not isinstance(attrs, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for k, v in attrs.items():
        if v is None or isinstance(v, (dict, list, tuple)):
            continue
        value = str(v).strip()
        if not value:
            continue
        key = str(k).strip().lower().replace(" ", "_").replace("-", "_")[:30]
        if not key:
            continue
        rows.append(
            {
                "namespace": _METAFIELD_NAMESPACE,
                "key": key,
                "type": "single_line_text_field",
                "value": value[:500],
            }
        )
    # Deterministic order on the NORMALIZED key (raw keys mix cases/spaces).
    rows.sort(key=lambda r: r["key"])
    return rows[:_MAX_METAFIELDS]


async def _set_product_metafields(
    db, product_gid: str, metafields: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """LIVE-only: upsert the product's attribute metafields via metafieldsSet
    (idempotent on owner+namespace+key), chunked at the Shopify per-call cap.
    Fail-SOFT: a metafield error must never undo/fail the product push itself --
    returns {"set": n, "errors": [...]} for the result/audit row."""
    set_count = 0
    errors: List[str] = []
    for i in range(0, len(metafields), _METAFIELDS_PER_CALL):
        chunk = [
            {**m, "ownerId": product_gid}
            for m in metafields[i : i + _METAFIELDS_PER_CALL]
        ]
        try:
            body = await _graphql(db, _METAFIELDS_SET, {"metafields": chunk})
            field_obj = (body.get("data") or {}).get("metafieldsSet") or {}
            errs = field_obj.get("userErrors") or []
            if errs:
                errors.extend(
                    f"{(e.get('field') or '?')}: {e.get('message')}" for e in errs
                )
            set_count += len(field_obj.get("metafields") or [])
        except Exception as e:  # noqa: BLE001 -- fail-soft side channel
            errors.append(str(e))
    return {"set": set_count, "errors": errors}


def _derive_options(variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Shopify productOptions (Color / Size) from the variant option axes
    that are actually present. Returns [] when no variant carries an option."""
    colors = [v.get("option_color") for v in variants if v.get("option_color")]
    sizes = [v.get("option_size") for v in variants if v.get("option_size")]
    options: List[Dict[str, Any]] = []
    if colors:
        options.append(
            {"name": "Color", "values": [{"name": c} for c in _dedupe(colors)]}
        )
    if sizes:
        options.append(
            {"name": "Size", "values": [{"name": s} for s in _dedupe(sizes)]}
        )
    return options


def _dedupe(seq: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _price_float(v: Any) -> float:
    """A usable positive price, else 0.0. Fail-soft on junk."""
    try:
        f = float(v)
        return f if f > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _resolve_variant_pricing(
    product: Dict[str, Any], variant: Dict[str, Any]
) -> Tuple[float, float]:
    """Resolve (selling_price, mrp) for ONE variant.

    Selling price: the variant's own discounted_price, else its mrp, else the
    ONLINE rule price on the ecom sub-doc (ecom.online_offer_price -- what the
    online discount engine writes for a no-variant product), else the parent
    product's offer_price / pricing.offer_price, else the product mrp.
    MRP (the compare-at side): the variant's compare_at_price, else its mrp, else
    the ecom online compare-at, else the product mrp. Returns 0.0 legs when nothing
    usable exists.

    NOTE (online discount engine): ecom.online_offer_price is preferred ABOVE the
    in-store offer_price ONLY when the online discount engine actually computed it
    (ecom.online_price_source in {"rule","manual"}). An unstamped / "none" online
    price is a hand-set BVI value or a stale save -- preferring it would silently
    FLIP a no-variant product's shipped price (e.g. from its in-store offer up to
    MRP) at the Phase-B cutover. When it is not engine-stamped we fall back to the
    existing in-store offer (pre-batch behaviour) so nothing flips unexpectedly. The
    engine NEVER writes offer_price, so in-store POS pricing is untouched. Variant-
    carrying products are unaffected: the variant's own discounted_price (which the
    engine writes) still wins first."""
    pricing = product.get("pricing") or {}
    ecom = product.get("ecom") or {}
    # Only trust the product-level online offer/compare-at when the engine stamped
    # it (a real rule- or manual-derived price); ignore unstamped / "none" values.
    _ecom_stamped = ecom.get("online_price_source") in ("rule", "manual")
    ecom_online_offer = _price_float(ecom.get("online_offer_price")) if _ecom_stamped else 0.0
    ecom_online_compare = (
        _price_float(ecom.get("online_compare_at_price")) if _ecom_stamped else 0.0
    )
    price = (
        _price_float(variant.get("discounted_price"))
        or _price_float(variant.get("mrp"))
        or ecom_online_offer
        or _price_float(product.get("offer_price"))
        or _price_float(pricing.get("offer_price"))
        or _price_float(product.get("mrp"))
        or _price_float(pricing.get("mrp"))
    )
    mrp = (
        _price_float(variant.get("compare_at_price"))
        or _price_float(variant.get("mrp"))
        or ecom_online_compare
        or _price_float(product.get("mrp"))
        or _price_float(pricing.get("mrp"))
    )
    return price, mrp


def _has_publishable_price(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]
) -> bool:
    """EVERY variant resolves to a positive selling price. Pure.

    The publish precondition for a press that did no seeding (an update whose
    variants all already carry their gid). One priceless variant is enough to
    put a 0.00 buy-button on the storefront, so this is `all`, not `any`. A
    product with no catalog_variants rows is judged on its single implied
    default variant, which _resolve_variant_pricing resolves from the product /
    ecom pricing."""
    rows = list(variants or []) or [{}]
    return all(_resolve_variant_pricing(product, v)[0] > 0 for v in rows)


def _variants_for_price_push(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Effective variant rows for a price/barcode push. Pure.

    The passed catalog_variants rows when there are any; otherwise ONE
    synthesized product-level pseudo-variant carrying the stored DEFAULT-variant
    gid (ecom.shopify_variant_id -- written back by the create-side seeding).
    A product with no catalog_variants rows maps to a single Shopify "Default
    Title" variant, and without this synthesis its price changes (e.g. a
    discount-rule recompute -- OS-016) could never ride the variant-price push:
    build_variant_price_inputs only reads the rows it is given, and
    _resolve_variant_pricing already falls back to the product/ecom pricing for
    an option-less row. UPDATE-only stays true: no stored default gid -> []
    (a clean noop downstream, never a create).

    The pseudo carries the product's gtin AND barcode as SEPARATE candidate
    fields (never pre-collapsed), so build_variant_price_inputs' _publishable_gtin
    gate (#948) evaluates them exactly as for a real variant -- an internally
    minted GS1 20-29 code in product.barcode is rejected, never shipped as a
    public GTIN."""
    rows = list(variants or [])
    if rows:
        return rows
    ecom = product.get("ecom") or {}
    default_gid = ecom.get("shopify_variant_id")
    if not default_gid:
        return []
    pseudo: Dict[str, Any] = {
        "shopify_variant_id": default_gid,
        "gtin": product.get("gtin"),
        "barcode": product.get("barcode"),
    }
    return [pseudo]


def _publishable_gtin(*candidates: Any) -> Optional[str]:
    """The first candidate that is a REAL GTIN, normalised. None when none is.

    This is the last gate before a value becomes customer-visible: whatever we
    put in Shopify's `ProductVariant.barcode` is republished into the Google
    and Meta Shopping feeds. An empty barcode is always safer than a wrong one
    -- a missing GTIN costs some feed match-rate, a WRONG GTIN gets the item
    rejected or matched to another manufacturer's product.

    Two behaviours worth calling out:
      - it picks the first VALID candidate, not the first non-empty one, so a
        junk value on the variant no longer shadows a good GTIN on the parent
        product (the old `or` chain stopped at the first truthy string);
      - `product["barcode"]` is in the fallback chain and, on many rows, holds
        our INTERNALLY minted GS1 20-29 code. classify_gtin rejects that whole
        prefix range as RESTRICTED, so an in-store barcode can no longer leak
        out as if it were a manufacturer GTIN.
    """
    for candidate in candidates:
        cleaned = sanitise_gtin(candidate)
        if cleaned:
            return cleaned
    return None


def build_variant_price_inputs(
    product: Dict[str, Any], variants: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], int]:
    """Build the ProductVariantsBulkInput rows for a price/barcode push. Pure.

    Per variant: id (the stored shopify_variant_id gid), price (selling),
    compareAtPrice (mrp when > price, else EXPLICIT null so a stale
    strikethrough on Shopify is cleared), barcode (the variant's `gtin` --
    the two-barcode model: gtin/barcode IS the GTIN pushed to Shopify;
    `store_barcode` is the physical join key and is NEVER pushed).

    SKIPS (counted, returned as the second tuple member):
      - variants with no stored shopify_variant_id -- they get their gid when
        the product's variants first sync; we never CREATE variants here.
      - variants with no resolvable positive price (never push a 0 price).
    """
    rows: List[Dict[str, Any]] = []
    skipped = 0
    for v in variants or []:
        gid = v.get("shopify_variant_id")
        if not gid:
            skipped += 1
            continue
        price, mrp = _resolve_variant_pricing(product, v)
        if price <= 0:
            skipped += 1
            continue
        row: Dict[str, Any] = {
            "id": _as_shopify_gid(gid, "ProductVariant"),
            "price": f"{price:.2f}",
            "compareAtPrice": f"{mrp:.2f}" if mrp > price else None,
        }
        barcode = _publishable_gtin(v.get("gtin"), v.get("barcode"))
        if barcode:
            row["barcode"] = barcode
        rows.append(row)
    return rows, skipped


# ---------------------------------------------------------------------------
# CREATE-side variant seeding (the price-0.00 / no-SKU fix)
# ---------------------------------------------------------------------------
# ProductInput has no price/sku, so a bare productCreate lands a product whose
# variant is unsellable-looking (0.00) and unjoinable (no SKU). These pure
# builders describe the variant state we WANT; _seed_variants_after_write applies
# it to the variants Shopify actually materialised.


def _norm_opt(value: Any) -> str:
    """Normalised option token used to join an IMS variant to a Shopify one."""
    return str(value or "").strip().lower()


def _variant_option_key(variant: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """(color, size) join key for an IMS catalog_variants row. The product-level
    pseudo-variant (None) keys to ("", ""), which is exactly what Shopify's
    "Default Title" variant keys to -- so a no-variant product maps 1:1 onto the
    single default variant."""
    v = variant or {}
    return (_norm_opt(v.get("option_color")), _norm_opt(v.get("option_size")))


def _node_option_key(node: Dict[str, Any]) -> Tuple[str, str]:
    """(color, size) join key for a Shopify ProductVariant node, read off its
    selectedOptions. Any option we do not model (incl. "Title"/"Default Title")
    contributes nothing, so a default variant keys to ("", "")."""
    color = size = ""
    for so in (node or {}).get("selectedOptions") or []:
        name = _norm_opt((so or {}).get("name"))
        if name == "color":
            color = _norm_opt(so.get("value"))
        elif name == "size":
            size = _norm_opt(so.get("value"))
    return (color, size)


def _node_inventory_item_gid(node: Optional[Dict[str, Any]]) -> Optional[str]:
    """The InventoryItem gid off a returned ProductVariant node
    (inventoryItem { id }), normalised to a full gid -- the oversell-guard
    stock-write-back target. None when the response does not carry it (an old
    canned body, a partial node): the write-back then leaves any existing
    mapping untouched (set-only, never cleared)."""
    inv = (node or {}).get("inventoryItem")
    if not isinstance(inv, dict):
        return None
    raw = inv.get("id")
    if not raw:
        return None
    return _as_shopify_gid(raw, "InventoryItem") or None


def _variant_option_values(variant: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """VariantOptionValueInput rows (used only when CREATING a variant Shopify
    did not auto-create). Empty for a product-level / option-less variant --
    such a variant can never be created, only updated."""
    v = variant or {}
    out: List[Dict[str, str]] = []
    if v.get("option_color"):
        out.append({"optionName": "Color", "name": str(v["option_color"])})
    if v.get("option_size"):
        out.append({"optionName": "Size", "name": str(v["option_size"])})
    return out


def build_variant_seed_rows(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """The DESIRED Shopify variant state for a product being created. Pure.

    One entry per IMS variant, or exactly ONE product-level entry when the
    product carries no catalog_variants row (the common eyewear case: a single
    Shopify "Default Title" variant). Each entry is
    {key, option_values, row, variant} where `row` is the price/sku part of a
    ProductVariantsBulkInput:
        price            the resolved selling price, "0.00" is NEVER sent
        compareAtPrice   the MRP when it is strictly above the price, else an
                         EXPLICIT None (GraphQL null) so a stale strikethrough
                         on Shopify is CLEARED -- the same contract as
                         build_variant_price_inputs
        barcode          the GTIN (variant gtin/barcode, else the product's) --
                         `store_barcode` is the physical join key and is never pushed
        inventoryItem.sku the IMS SKU (variant sku, else the product sku) -- in
                         the 2024-04+ product model the SKU lives on the
                         inventory item, NOT on the variant

    An entry with NEITHER a usable price NOR a SKU is dropped: there would be
    nothing to say about that variant."""
    rows: List[Dict[str, Any]] = []
    source: List[Optional[Dict[str, Any]]] = list(variants or []) or [None]
    for v in source:
        vd = v or {}
        price, mrp = _resolve_variant_pricing(product, vd)
        sku = str(vd.get("sku") or product.get("sku") or "").strip()
        barcode = _publishable_gtin(
            vd.get("gtin"),
            vd.get("barcode"),
            product.get("gtin"),
            product.get("barcode"),
        )
        row: Dict[str, Any] = {}
        if price > 0:
            row["price"] = f"{price:.2f}"
            if mrp > price:
                row["compareAtPrice"] = f"{mrp:.2f}"
            else:
                # EXPLICIT GraphQL null, byte-matching build_variant_price_inputs'
                # contract: when the MRP no longer exceeds the selling price, a
                # stale strikethrough on Shopify must be CLEARED, not kept. On a
                # flag-ON update the seeding step REPLACES push_variant_prices
                # (seeded=True skips it), so omitting the field here would
                # silently lose today's clearing behaviour and leave a fake
                # "was <old MRP>" above the real price -- an MRP-display
                # compliance issue (adversarial-panel must-fix 3).
                row["compareAtPrice"] = None
        if sku:
            row["inventoryItem"] = {"sku": sku}
        if barcode:
            row["barcode"] = str(barcode)
        if not row.get("price") and not row.get("inventoryItem"):
            # No price and no SKU -> nothing worth a mutation for this variant.
            continue
        rows.append(
            {
                "key": _variant_option_key(v),
                "option_values": _variant_option_values(v),
                "row": row,
                "variant": v,  # None for the product-level pseudo-variant
            }
        )
    return rows


def plan_variant_seed(
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]] = None,
    *,
    repair_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """The SIMULATED (dry-run) view of the seeding step: the exact rows that
    WOULD be applied to the new Shopify variants, with no gid yet (Shopify mints
    those at create time). None when there is nothing to seed.

    `repair_only=True` restricts the plan to the UNSEEDED subset
    (build_repair_seed_rows) instead of every row -- the repair-only update
    path (independent of SHOPIFY_PUSH_PRICE_ON_UPDATE) must never show/apply
    a re-price for a row that already carries a stored gid."""
    rows = (
        build_repair_seed_rows(product, variants)
        if repair_only
        else build_variant_seed_rows(product, variants)
    )
    if not rows:
        return None
    planned: List[Dict[str, Any]] = []
    for r in rows:
        entry = dict(r["row"])
        if r["option_values"]:
            entry["optionValues"] = r["option_values"]
        planned.append(entry)
    return {
        "variants": planned,
        "note": (
            "price/compareAtPrice/barcode/sku are applied to the variants "
            "Shopify creates (productVariantsBulkUpdate), and any remaining IMS "
            "variant is created (productVariantsBulkCreate)"
        ),
    }


def _row_has_gid(v: Optional[Dict[str, Any]], ecom: Dict[str, Any]) -> bool:
    """True when this ONE seed-row target already carries a stored gid: a real
    IMS catalog_variants row's own shopify_variant_id/shopify_inventory_item_id,
    or -- for the product-level pseudo-variant (v is None, a no-variant-row
    product) -- the product's ecom fallback fields. Pure, row-level (never
    aggregated across a product's other rows)."""
    if v is not None:
        return bool(
            str(v.get("shopify_variant_id") or "").strip()
            or str(v.get("shopify_inventory_item_id") or "").strip()
        )
    return bool(
        str(ecom.get("shopify_variant_id") or "").strip()
        or str(ecom.get("shopify_inventory_item_id") or "").strip()
    )


def _needs_repair(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]
) -> bool:
    """True when AT LEAST ONE variant target of this product -- a real
    catalog_variants row, or the product-level pseudo-variant for a
    no-variant-row product -- has NEVER been seeded (carries no stored
    shopify_variant_id / shopify_inventory_item_id anywhere).

    "Never seeded" == the create-side seeding either never ran or its bulk
    write failed for that specific row, so its Shopify variant still sits at
    the auto-created 0.00 / no-SKU / no-stock-target.

    ROW-aware, deliberately NOT `all(...)`/product-aware (panel fix-round,
    #955): a multi-variant product where a PARTIAL bulk-create leaves some
    rows seeded and others not (see _seed_variants_after_write's harvest of a
    partially-successful productVariantsBulkCreate response) must still be
    flagged for repair on every later push until EVERY row has a gid. The
    earlier `not any(...)` formulation flipped False the moment ANY single
    row acquired a gid, permanently masking the remaining stranded row(s)
    from ever being retried without the owner globally re-arming
    SHOPIFY_PUSH_PRICE_ON_UPDATE -- exactly the ~4,400-product blast radius
    this repair-only mechanism exists to avoid. Pure.

    Used to run REPAIR-only seeding on an UPDATE, INDEPENDENT of
    SHOPIFY_PUSH_PRICE_ON_UPDATE. The caller pairs this with
    build_repair_seed_rows / plan_variant_seed(repair_only=True) /
    _seed_variants_after_write(repair_only=True) so ONLY the still-unseeded
    row(s) are ever submitted for (re)seeding -- an already-seeded row is
    never re-touched (re-priced/re-barcoded) by a repair pass."""
    ecom = product.get("ecom") or {}
    source: List[Optional[Dict[str, Any]]] = list(variants or []) or [None]
    return any(not _row_has_gid(v, ecom) for v in source)


def build_repair_seed_rows(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Like build_variant_seed_rows, but restricted to the row(s) that have
    NEVER been seeded (see _row_has_gid / _needs_repair). Used by the
    repair-only update path so it can (re)seed a stranded variant WITHOUT
    re-touching -- re-pricing, re-barcoding -- any row that already carries a
    stored gid. Pure.

    An already-fully-seeded product (every row/the pseudo-variant has a gid)
    returns [] here even though build_variant_seed_rows(product, variants)
    would still happily rebuild a full plan for it -- that full-reseed
    behaviour is reserved for the CREATE path and the owner's explicit
    SHOPIFY_PUSH_PRICE_ON_UPDATE opt-in, both of which call
    build_variant_seed_rows directly instead of this function."""
    ecom = product.get("ecom") or {}
    source: List[Optional[Dict[str, Any]]] = list(variants or []) or [None]
    unseeded = [v for v in source if not _row_has_gid(v, ecom)]
    if not unseeded:
        return []
    # `unseeded` is either a sublist of real IMS variant dicts, or exactly
    # [None] (the pseudo-variant case) -- both shapes are exactly what
    # build_variant_seed_rows expects as its own `variants` argument (a bare
    # [None] round-trips through its `list(variants or []) or [None]` guard
    # identically to passing None/[] outright), so no special-casing needed.
    return build_variant_seed_rows(product, unseeded)


def _assign_seed_rows(
    seed_rows: List[Dict[str, Any]], nodes: Optional[List[Dict[str, Any]]]
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Optional[Dict[str, Any]]],
    List[Tuple[Optional[Dict[str, Any]], str, Optional[str]]],
    int,
]:
    """Split the desired rows against the variants Shopify actually created.
    Pure. Returns (update_rows, create_rows, create_variants, pairs, skipped):

      update_rows      ProductVariantsBulkInput rows carrying the matched gid
      create_rows      rows for IMS variants Shopify did NOT create (productCreate
                       only ever materialises one variant)
      create_variants  the IMS variant docs aligned 1:1 with create_rows
      pairs            (ims_variant_or_None, gid, inventory_item_gid_or_None)
                       for the matched ones -> write-back. The third member is
                       the node's inventoryItem gid -- the oversell-guard stock
                       target -- None when the response did not carry it.
      skipped          rows we can neither update nor create (no gid, no options)

    MATCHING ORDER (adversarial-panel must-fix 2): a row whose IMS variant
    already stores a shopify_variant_id that appears among the returned nodes
    is paired on that GID FIRST, regardless of option-label drift (an IMS
    option rename, BVI-era spelling like Grey/Gray). Only rows without a
    stored-and-present gid fall through to (color,size) option-key matching.
    A row whose stored gid is present in the nodes can therefore NEVER reach
    productVariantsBulkCreate -- the drift case previously minted a duplicate
    live variant and re-pointed the row's stock target at it, leaving the old
    variant sellable at a stale price with its stock never synced again."""
    pool: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    unmatched: List[Dict[str, Any]] = []
    node_by_gid: Dict[str, Dict[str, Any]] = {}
    for n in nodes or []:
        if isinstance(n, dict) and n.get("id"):
            pool.setdefault(_node_option_key(n), []).append(n)
            unmatched.append(n)
            node_by_gid[_as_shopify_gid(n.get("id"), "ProductVariant")] = n

    update_rows: List[Dict[str, Any]] = []
    create_rows: List[Dict[str, Any]] = []
    create_variants: List[Optional[Dict[str, Any]]] = []
    pairs: List[Tuple[Optional[Dict[str, Any]], str, Optional[str]]] = []
    skipped = 0

    # PASS 1 -- gid-first. matched_by_row keeps seed-row order so pairs /
    # update_rows are emitted in the same order as before (pairs[0] stays the
    # first seed row -- the default-variant contract used by the ecom
    # write-back).
    matched_by_row: Dict[int, Tuple[str, Optional[str]]] = {}
    remaining: List[Tuple[int, Dict[str, Any]]] = []
    for idx, r in enumerate(seed_rows):
        stored = (
            (r["variant"] or {}).get("shopify_variant_id")
            if r["variant"] is not None
            else None
        )
        gid = _as_shopify_gid(stored, "ProductVariant") if stored else ""
        node = node_by_gid.pop(gid, None) if gid else None
        if node is not None:
            # Consume the node everywhere so option matching cannot reuse it.
            if node in unmatched:
                unmatched.remove(node)
            bucket = pool.get(_node_option_key(node))
            if bucket and node in bucket:
                bucket.remove(node)
            matched_by_row[idx] = (gid, _node_inventory_item_gid(node))
        else:
            remaining.append((idx, r))

    # Single remaining row / single remaining node: trust the 1:1 pairing even
    # if the option labels do not line up (e.g. Shopify kept a "Title" option).
    lone = len(remaining) == 1 and len(unmatched) == 1

    # PASS 2 -- option-key matching for the rows without a stored-and-present
    # gid (unchanged semantics); only these may fall through to create/skip.
    for idx, r in remaining:
        bucket = pool.get(r["key"]) or []
        node = bucket.pop(0) if bucket else (unmatched[0] if lone else None)
        if node is not None:
            if node in unmatched:
                unmatched.remove(node)
            gid = _as_shopify_gid(node.get("id"), "ProductVariant")
            matched_by_row[idx] = (gid, _node_inventory_item_gid(node))
        elif r["option_values"]:
            create_rows.append({"optionValues": r["option_values"], **r["row"]})
            create_variants.append(r["variant"])
        else:
            skipped += 1

    for idx, r in enumerate(seed_rows):
        got = matched_by_row.get(idx)
        if got is None:
            continue
        gid, inv_gid = got
        update_rows.append({"id": gid, **r["row"]})
        pairs.append((r["variant"], gid, inv_gid))
    return update_rows, create_rows, create_variants, pairs, skipped


async def _seed_variants_after_write(
    db,
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]],
    product_gid: str,
    nodes: Optional[List[Dict[str, Any]]],
    *,
    repair_only: bool = False,
) -> Optional[Dict[str, Any]]:
    """LIVE-only: give the freshly created Shopify variants their price / MRP /
    barcode / SKU, create any remaining IMS variant, and write every gid back --
    BOTH the ProductVariant gid (a later price push's handle) and the
    InventoryItem gid (the oversell-guard stock write-back's target).

    Fail-SOFT side channel, exactly like metafields: an error is reported in the
    returned summary and NEVER flips the product push's ok (the product itself
    was created successfully; a re-push repairs the variants). Returns None when
    there is nothing to seed (no price and no SKU anywhere).

    `repair_only=True` (the flag-independent update path, #955 fix-round)
    restricts `seed_rows` to build_repair_seed_rows' UNSEEDED subset instead
    of every row -- so a row that already carries a stored gid can never be
    re-priced/re-barcoded by a repair pass, and only the still-stranded
    row(s) go through _assign_seed_rows' matching this call."""
    seed_rows = (
        build_repair_seed_rows(product, variants)
        if repair_only
        else build_variant_seed_rows(product, variants)
    )
    if not seed_rows:
        return None
    update_rows, create_rows, create_variants, pairs, skipped = _assign_seed_rows(
        seed_rows, nodes
    )
    summary: Dict[str, Any] = {
        "updated": 0,
        "created": 0,
        "skipped_unmatched": skipped,
        "errors": [],
        "default_variant_gid": None,
        "variant_gids": [],
        # Oversell-guard capture: every InventoryItem gid persisted (aligned
        # 1:1 with variant_gids; None where the response carried none), plus
        # the PRODUCT-LEVEL one (set ONLY for a no-variant-row product, whose
        # single default variant is the product itself -- see push_product's
        # ecom fallback write-back).
        "inventory_item_gids": [],
        "product_level_inventory_item_gid": None,
        # How many seed rows actually carried a selling price. The publish
        # precondition (must-fix 1) requires this to be > 0: a SKU-only seed
        # means Shopify's variant is still at 0.00 and must never go visible.
        "priced_rows": sum(1 for r in seed_rows if r["row"].get("price")),
    }

    for i in range(0, len(update_rows), _VARIANTS_PER_CALL):
        chunk = update_rows[i : i + _VARIANTS_PER_CALL]
        try:
            body = await _graphql(
                db, _VARIANTS_BULK_UPDATE, {"productId": product_gid, "variants": chunk}
            )
            err = _user_errors(body, "productVariantsBulkUpdate")
            if err:
                summary["errors"].append(err)
            else:
                summary["updated"] += len(chunk)
        except Exception as e:  # noqa: BLE001 -- fail-soft side channel
            summary["errors"].append(str(e))

    created_nodes: List[Dict[str, Any]] = []
    for i in range(0, len(create_rows), _VARIANTS_PER_CALL):
        chunk = create_rows[i : i + _VARIANTS_PER_CALL]
        try:
            body = await _graphql(
                db, _VARIANTS_BULK_CREATE, {"productId": product_gid, "variants": chunk}
            )
            err = _user_errors(body, "productVariantsBulkCreate")
            if err:
                summary["errors"].append(err)
            # Harvest whatever the call DID create even on a partial-error
            # response. productVariantsBulkCreate can return BOTH userErrors AND
            # a non-empty productVariants list (some rows rejected, others
            # created). The old `continue` on any error discarded that list, so
            # the variants Shopify HAD created were orphaned: their
            # ProductVariant / InventoryItem gids were never written back,
            # leaving live variants IMS could neither re-price nor oversell-guard
            # again. We now always read the returned nodes (empty on a total
            # failure -> nothing harvested), then match + write them back below.
            made = (
                (body.get("data") or {}).get("productVariantsBulkCreate") or {}
            ).get("productVariants") or []
            created_nodes.extend(n for n in made if isinstance(n, dict) and n.get("id"))
        except Exception as e:  # noqa: BLE001 -- fail-soft side channel
            summary["errors"].append(str(e))
    summary["created"] = len(created_nodes)

    # Match the newly created variants back to their IMS rows (same option key)
    # so their gids are persisted too.
    if created_nodes:
        by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for n in created_nodes:
            by_key.setdefault(_node_option_key(n), []).append(n)
        for v in create_variants:
            bucket = by_key.get(_variant_option_key(v)) or []
            if bucket:
                made_node = bucket.pop(0)
                pairs.append(
                    (
                        v,
                        _as_shopify_gid(made_node.get("id"), "ProductVariant"),
                        _node_inventory_item_gid(made_node),
                    )
                )

    for variant_doc, gid, inventory_item_gid in pairs:
        summary["variant_gids"].append(gid)
        summary["inventory_item_gids"].append(inventory_item_gid)
        if variant_doc:
            _writeback_variant(db, variant_doc, gid, inventory_item_gid)
        elif inventory_item_gid:
            # The product-level pseudo-variant (product has NO catalog_variants
            # rows): there is no variant row to stamp, so its InventoryItem gid
            # goes onto the PRODUCT's ecom sub-doc instead (push_product passes
            # it to _writeback_product). NEVER set for a product that HAS
            # variant rows -- stamping variant #1's inventory item on the
            # parent would hand a parent-sku stock lookup the WRONG variant's
            # inventory target.
            summary["product_level_inventory_item_gid"] = inventory_item_gid
    if pairs:
        # The FIRST pair is the default variant (the product-level row for a
        # no-variant product) -- the one a later price push needs.
        summary["default_variant_gid"] = pairs[0][1]
    return summary


# ---------------------------------------------------------------------------
# Sales-channel publish -- the door that makes an ACTIVE product visible
# ---------------------------------------------------------------------------


async def _resolve_online_store_publication_id(db) -> Optional[str]:
    """The Online Store publication gid: the pinned env value when set, else a
    one-time `publications` lookup (cached per process). Fail-soft -> None (the
    publish step then reports why instead of raising)."""
    pinned = (os.getenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID") or "").strip()
    if pinned:
        return _as_shopify_gid(pinned, "Publication")
    cached = _publication_id_cache.get(_ONLINE_STORE_PUBLICATION_NAME)
    if cached:
        return cached
    try:
        body = await _graphql(db, _PUBLICATIONS_QUERY, {})
    except Exception as e:  # noqa: BLE001 -- fail-soft side channel
        logger.warning("[SHOPIFY_PUSH] publication lookup failed: %s", e)
        return None
    nodes = ((body.get("data") or {}).get("publications") or {}).get("nodes") or []
    for n in nodes:
        if str((n or {}).get("name") or "").strip().lower() == (
            _ONLINE_STORE_PUBLICATION_NAME.lower()
        ):
            gid = n.get("id")
            if gid:
                _publication_id_cache[_ONLINE_STORE_PUBLICATION_NAME] = gid
                return gid
    return None


async def _publish_to_online_store(db, product_gid: str) -> Dict[str, Any]:
    """LIVE-only: publish ONE product to the Online Store sales channel so an
    ACTIVE product is actually visible on the storefront. Fail-SOFT side channel
    (reported, never flips the push's ok). Called on EVERY product push whose
    payload is ACTIVE, create or update -- publishablePublish is idempotent, so
    re-publishing an already-published product is a no-op. The caller withholds
    it unless the price and the photograph are both provably right."""
    pub_id = await _resolve_online_store_publication_id(db)
    if not pub_id:
        return {
            "published": False,
            "error": (
                "Online Store publication id not resolved (set "
                "SHOPIFY_ONLINE_STORE_PUBLICATION_ID or grant read_publications)"
            ),
        }
    try:
        body = await _graphql(
            db,
            _PUBLISHABLE_PUBLISH,
            {"id": product_gid, "input": [{"publicationId": pub_id}]},
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft side channel
        return {"published": False, "publication_id": pub_id, "error": str(e)}
    err = _user_errors(body, "publishablePublish")
    if err:
        return {"published": False, "publication_id": pub_id, "error": err}
    return {"published": True, "publication_id": pub_id}


def build_collection_input(collection: Dict[str, Any]) -> Dict[str, Any]:
    """Build a Shopify CollectionInput from an ecom_collections doc. A SMART
    collection's rules become a ruleSet; a CUSTOM collection's manual SKU list is
    carried as a separate add step (not modelled in CollectionInput here -- the
    Phase-6 membership push handles collectionAddProducts)."""
    inp: Dict[str, Any] = {
        "title": collection.get("title") or collection.get("handle"),
        "handle": collection.get("handle"),
    }
    sid = collection.get("shopify_collection_id")
    if sid:
        inp["id"] = _as_shopify_gid(sid, "Collection")
    desc = collection.get("description_html") or collection.get("description")
    if desc:
        inp["descriptionHtml"] = desc
    if collection.get("template_suffix"):
        inp["templateSuffix"] = collection["template_suffix"]
    if collection.get("seo_title") or collection.get("seo_description"):
        inp["seo"] = {
            "title": collection.get("seo_title") or collection.get("title") or "",
            "description": collection.get("seo_description") or "",
        }
    if collection.get("sort_order"):
        inp["sortOrder"] = collection["sort_order"]
    # Collection hero image (parity with BVI's updateCollection, which pushed
    # image:{src,altText}). `image_url` is the stored collection image; the
    # `banner_image` metafield is a separate storefront concern, not the
    # Shopify CollectionInput.image.
    image_src = collection.get("image_url")
    if image_src:
        inp["image"] = {
            "src": image_src,
            "altText": collection.get("image_alt") or collection.get("title") or "",
        }
    if (collection.get("collection_type") or "").upper() == "SMART":
        rules = _build_rule_set(collection.get("rules") or [])
        if rules:
            inp["ruleSet"] = {
                "appliedDisjunctively": bool(collection.get("disjunctive", False)),
                "rules": rules,
            }
    return inp


# Map IMS smart-rule fields -> Shopify CollectionRuleColumn + relation.
_RULE_COLUMN = {
    "brand": "VENDOR",
    "vendor": "VENDOR",
    "category": "TYPE",
    "product_type": "TYPE",
    "type": "TYPE",
    "tag": "TAG",
    "title": "TITLE",
}
_RULE_RELATION = {
    "EQUALS": "EQUALS",
    "CONTAINS": "CONTAINS",
}


def _build_rule_set(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Translate IMS {field, relation, value} smart rules into Shopify
    CollectionRuleInput {column, relation, condition}. Unknown columns are
    skipped (we never push a rule Shopify can't evaluate)."""
    out: List[Dict[str, Any]] = []
    for r in rules:
        col = _RULE_COLUMN.get(str(r.get("field") or "").lower())
        if not col:
            continue
        rel = _RULE_RELATION.get(str(r.get("relation") or "EQUALS").upper(), "EQUALS")
        out.append(
            {"column": col, "relation": rel, "condition": str(r.get("value") or "")}
        )
    return out


def _member_product_gids(db, collection: Dict[str, Any]) -> Tuple[List[str], int]:
    """Resolve a CUSTOM collection's manual member SKUs -> their parent products'
    Shopify gids (catalog_products.ecom.shopify_product_id), in position order.

    SMART collections have NO manual membership (Shopify derives their set from
    the ruleSet) -> ([], 0). A member whose product isn't on Shopify yet is
    SKIPPED (counted): it joins the collection when that product first syncs; we
    never create a product from here. Fail-soft -> ([], 0)."""
    if str(collection.get("collection_type") or "CUSTOM").upper() != "CUSTOM":
        return [], 0
    members = sorted(
        (collection.get("products") or []),
        key=lambda p: int((p or {}).get("position", 0) or 0),
    )
    skus = [p.get("sku") for p in members if isinstance(p, dict) and p.get("sku")]
    gids: List[str] = []
    skipped = 0
    for sku in skus:
        gid = None
        try:
            doc = db["catalog_products"].find_one({"sku": sku}) if db is not None else None
            if doc is not None:
                gid = (doc.get("ecom") or {}).get("shopify_product_id")
        except Exception:  # noqa: BLE001 -- one bad lookup never blocks the rest
            gid = None
        if gid:
            gids.append(_as_shopify_gid(gid, "Product"))
        else:
            skipped += 1
    return gids, skipped


async def _push_collection_membership(
    db, collection_gid: str, product_gids: List[str]
) -> Dict[str, Any]:
    """LIVE-only: attach a CUSTOM collection's manual members via
    collectionAddProducts, chunked at the per-call cap. Fail-SOFT side channel --
    a membership error is reported but NEVER flips the collection push's ok
    (mirrors the metafields / variant-prices contract). Returns a summary dict."""
    added = 0
    errors: List[str] = []
    for i in range(0, len(product_gids), _COLLECTION_PRODUCTS_PER_CALL):
        chunk = product_gids[i : i + _COLLECTION_PRODUCTS_PER_CALL]
        try:
            body = await _graphql(
                db, _COLLECTION_ADD_PRODUCTS, {"id": collection_gid, "productIds": chunk}
            )
            err = _user_errors(body, "collectionAddProducts")
            if err:
                errors.append(err)
            else:
                added += len(chunk)
        except Exception as e:  # noqa: BLE001 -- fail-soft side channel
            errors.append(str(e))
    return {"added": added, "errors": errors}


def build_menu_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Recursively map the IMS ecom_menus item tree -> Shopify MenuItem*Input.
    Each node carries title + type (+ url/resourceId) and its children. Used for
    BOTH create + update (the input shapes are field-compatible for our subset)."""
    out: List[Dict[str, Any]] = []
    for node in items or []:
        item: Dict[str, Any] = {
            "title": node.get("title") or "",
            "type": str(node.get("item_type") or "HTTP").upper(),
        }
        if node.get("url"):
            item["url"] = node["url"]
        if node.get("resource_id"):
            item["resourceId"] = _as_shopify_gid(node["resource_id"], "Collection")
        kids = node.get("children") or []
        if kids:
            item["items"] = build_menu_items(kids)
        out.append(item)
    return out


def product_photo_urls(product: Dict[str, Any]) -> List[str]:
    """Every usable PHOTOGRAPH URL on a catalog product doc, in display order.
    Pure; deduped; never raises.

    This is the single answer to "does this product have a photograph?" -- the
    owner's publish rule ("no photo, no publish") and the media actually sent to
    Shopify are both driven from THIS list, so the check and the payload can
    never disagree.

    Field precedence mirrors the rest of the app (catalogue_pdf._image_url_of,
    products.list_products' image_url alias): the singular `image_url`, then the
    `images[]` array (strings or {url|src} dicts), then a bare `image`.

    ONLY absolute http(s) URLs count. Shopify pulls the bytes over the internet
    from the URL we hand it; a private /uploads/... path is unfetchable, so
    counting one as a photograph would publish exactly the empty grey box the
    rule exists to prevent (the same rule online_sync_health.uploads_image_audit
    already flags rows on).

    NOTE: this deliberately does NOT read the `product_images` design queue.
    Those rows push on their own, LATER press (push_image), and a photo that
    arrives after the product is already visible does not protect the
    storefront. A product whose only photo lives in the design queue is refused
    rather than published bare -- conservative, and the operator fixes it by
    putting the photo on the product."""
    out: List[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, dict):
            value = value.get("url") or value.get("src")
        if not isinstance(value, str):
            return
        url = value.strip()
        if url.lower().startswith(("http://", "https://")) and url not in out:
            out.append(url)

    _add(product.get("image_url"))
    imgs = product.get("images")
    if isinstance(imgs, (list, tuple)):
        for item in imgs:
            _add(item)
    _add(product.get("image"))
    return out


async def _attach_product_photos(
    db, product_gid: str, urls: List[str]
) -> Dict[str, Any]:
    """LIVE-only: attach the product's photographs to the Shopify product in the
    SAME press that wrote the product (productCreateMedia). Fail-SOFT side
    channel -- reported on the result, never flips the push's ok -- but the
    caller WITHHOLDS the publish when nothing attached, so a media failure
    leaves an invisible product rather than a visible grey box.

    Only ever called when the Shopify product carries no media yet, so a
    re-press can never pile a duplicate copy of the same photo onto a live
    listing."""
    media = build_media_inputs([{"url": u} for u in urls])
    if not media:
        return {"attached": 0, "error": "no usable photograph"}
    try:
        body = await _graphql(
            db, _PRODUCT_CREATE_MEDIA, {"productId": product_gid, "media": media}
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft side channel
        return {"attached": 0, "error": str(e)}
    err = _user_errors_media(body)
    if err:
        return {"attached": 0, "error": err}
    nodes = ((body.get("data") or {}).get("productCreateMedia") or {}).get("media") or []
    return {"attached": len([n for n in nodes if (n or {}).get("id")])}


def build_media_inputs(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Shopify CreateMediaInput[] from APPROVED product_images. Prefer the
    designer's edited asset; fall back to the source url."""
    out: List[Dict[str, Any]] = []
    for img in images:
        src = img.get("edited_url") or img.get("url")
        if not src:
            continue
        out.append(
            {
                "originalSource": src,
                "alt": img.get("alt_text") or "",
                "mediaContentType": "IMAGE",
            }
        )
    return out


# ===========================================================================
# Write-back helpers (idempotency) -- store the Shopify gid on the IMS doc
# ===========================================================================


def _writeback_product(
    db,
    product_id: str,
    shopify_id: str,
    variant_gid: Optional[str] = None,
    inventory_item_gid: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    """Persist ecom.shopify_product_id (+ stamps) on the catalog_products doc and
    clear the dirty flag, for idempotent re-push.

    SYNC PATH -- this write only ever sets `locally_modified` to False, and must
    NEVER set it to True. It is the push's own book-keeping (the Shopify gid it
    just minted), not a human catalogue edit. Queuing from anywhere on the
    Shopify sync side is the ping-pong hazard: push -> write-back marks dirty ->
    the next sweep pushes it again -> forever, against a LIVE storefront. The
    catalogue-edit doors that DO queue are product_master._build_pim_doc (born
    dirty on create) and catalog._save_catalog_product (dirty by default, with an
    explicit mark_dirty=False on the non-catalogue writes).

    `variant_gid` (optional) additionally persists ecom.shopify_variant_id -- the
    DEFAULT Shopify variant of this product. Without it a product created by IMS
    could never have its price corrected: ProductInput carries no price, so the
    only handle on the money is the variant gid. It is only ever SET, never
    cleared, so a call without it leaves an existing mapping intact.

    `inventory_item_gid` (optional) additionally persists
    ecom.shopify_inventory_item_id -- the oversell-guard stock target for a
    product with NO catalog_variants rows. This is the documented resolver
    fallback (online_catalog.inventory_items_for_skus /
    online_variant_targets_for_skus and online_sync_health.
    _inventory_item_id_for_sku all read ecom.shopify_inventory_item_id when no
    variant row matches a SKU). The caller only ever passes it for the
    product-level pseudo-variant, and it too is set-only, never cleared.

    `status` (optional) records what the storefront now shows: "PUBLISHED" after
    a successful sales-channel publish, "DRAFT" after a take-down. ecom.status is
    READ in six places (the Online Store screen's DRAFT / PUBLISHED cards, the
    storefront-visibility helpers in online_catalog / buy_desk) and before this
    was written "PUBLISHED" in ZERO -- so IMS said DRAFT about a product that was
    live. Set-only: None leaves the stored value alone.

    We READ-MERGE-WRITE the whole `ecom` sub-doc (read the doc, mutate the ecom
    dict in Python, $set ecom back) rather than `$set {"ecom.shopify_product_id":
    ...}`. Both are correct on real Mongo, but the merge-write also works on the
    in-memory MockCollection (which doesn't model dot-notation $set) AND keeps the
    sibling ecom fields intact (status/handle/seo) because the merge is explicit.
    Fail-soft: any error is logged, never raised (the Shopify write already
    succeeded)."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        ecom["shopify_product_id"] = shopify_id
        if variant_gid:
            ecom["shopify_variant_id"] = variant_gid
        if inventory_item_gid:
            ecom["shopify_inventory_item_id"] = inventory_item_gid
        if status:
            ecom["status"] = status
            # THE TAKE-DOWN MARKER. Delist writes DRAFT and clears the dirty
            # flag, but build_product_input maps everything except ARCHIVED to
            # ACTIVE -- so the only thing holding a product down was the flag
            # being false, and any catalogue edit re-queues it for the next
            # sweep to silently re-list mid-fix. Stamped here (and cleared by a
            # successful publish, the only writer of PUBLISHED) so the SWEEP can
            # skip it until a human presses that one product explicitly.
            if status == "DRAFT":
                ecom["taken_down_at"] = _now()
            elif status == "PUBLISHED":
                ecom.pop("taken_down_at", None)
        ecom["last_pushed_at"] = _now()
        ecom["locally_modified"] = False
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] product write-back failed {product_id}: {e}")


def _requeue_unpublished(db, product_id: str) -> None:
    """Put a row BACK in the push queue after a press that reached Shopify but
    did NOT make the product visible (publish withheld: unpriced, the photograph
    did not attach, the Online Store publication could not be resolved).

    THIS IS NOT THE PING-PONG HAZARD. _writeback_product must never set the flag,
    because that write is the push's own book-keeping and would re-queue a press
    that fully succeeded -- forever. This is the opposite case: the press did NOT
    do what it was pressed for. Clearing the flag anyway would leave the product
    ON Shopify, INVISIBLE, and OUT of the queue -- `pending: 0` next to an empty
    brand page, which is the exact lie this whole change exists to end. It
    re-queues ONLY on the not-published branch, so a successful publish still
    drains the queue (the control test), and the batch cap bounds how often a
    stubbornly-unpublishable row can be retried per press.

    Read-merge-write of the whole ecom sub-doc (the _writeback_product idiom).
    Fail-soft: any error is logged, never raised."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        ecom["locally_modified"] = True
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] re-queue failed {product_id}: {e}")


def _writeback_variant(
    db,
    variant: Dict[str, Any],
    shopify_variant_id: str,
    inventory_item_gid: Optional[str] = None,
) -> bool:
    """Persist shopify_variant_id (+ shopify_inventory_item_id when the response
    carried it) on the catalog_variants row, keyed on `sku` (its primary
    identity; `variant_id` is the fallback). The variant gid is what makes the
    EXISTING price push (build_variant_price_inputs, which skips gid-less
    variants) able to repair a price later; the InventoryItem gid is what the
    oversell-guard stock write-back resolver reads
    (catalog_variants.shopify_inventory_item_id) to sync the listed quantity
    down after an in-store sale. `inventory_item_gid` is set-only: when None
    (an old canned response, a partial node) any existing mapping is left
    untouched, never cleared. Returns True iff a row was written.
    Fail-soft: never raises -- the Shopify write already succeeded."""
    filt: Optional[Dict[str, Any]] = None
    if (variant or {}).get("sku"):
        filt = {"sku": variant["sku"]}
    elif (variant or {}).get("variant_id"):
        filt = {"variant_id": variant["variant_id"]}
    if filt is None:
        return False
    update: Dict[str, Any] = {
        "shopify_variant_id": shopify_variant_id,
        "updated_at": _now(),
    }
    if inventory_item_gid:
        update["shopify_inventory_item_id"] = inventory_item_gid
    try:
        res = db["catalog_variants"].update_one(filt, {"$set": update})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] variant write-back failed {filt}: {e}")
        return False
    touched = getattr(res, "matched_count", None)
    if touched is None:
        touched = getattr(res, "modified_count", 0)
    return bool(touched)


def _writeback_simple(
    db,
    collection_name: str,
    id_field: str,
    doc_id: str,
    shopify_field: str,
    shopify_id: str,
) -> None:
    """Generic gid write-back for collection/menu docs: set the shopify id field,
    clear locally_modified, stamp last_synced_at. Fail-soft."""
    try:
        coll = db[collection_name]
        coll.update_one(
            {id_field: doc_id},
            {
                "$set": {
                    shopify_field: shopify_id,
                    "locally_modified": False,
                    "last_synced_at": _now(),
                }
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[SHOPIFY_PUSH] {collection_name} write-back failed {doc_id}: {e}"
        )


# ===========================================================================
# PUSH FUNCTIONS -- one per entity. DARK by default; LIVE only behind the gates.
# ===========================================================================


async def push_product(
    db,
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]] = None,
    blocked: Optional[bool] = None,
) -> PushResult:
    """Push a catalog product (+ its ecom sub-doc + variants) to Shopify.

    DARK by default -> returns a SIMULATED dry-run plan with the full ProductInput
    and NO network call. LIVE only when all three gates pass: then productCreate
    (no stored gid) or productUpdate (gid present), with the new gid written back
    for idempotency. Never raises.

    ``blocked`` is an OPTIONAL precomputed block classification:
      * ``None`` (default, single-push route): classify HERE via the STRICT
        variant. An UNKNOWN (block config unreadable) FAILS CLOSED -- the push is
        skipped so a DB blip can never let a contractually-banned product reach
        Shopify (finding #18).
      * ``True`` / ``False``: supplied by the /all-pending sweep, which resolves
        the blocked set ONCE for the whole batch (finding #20 -- no per-product
        re-scan). The sweep passes ``None`` (not ``False``) when it could not
        verify the config, so this fail-closed skip still applies."""
    pid = product.get("id") or product.get("product_id")
    # Hub Phase 5: push-lock is the FIRST gate -- a locked brand is NEVER pushed,
    # before the dark/live gate (fail-closed).
    _lock = push_lock_reason(db, "product", product)
    if _lock:
        return _blocked_result("product", pid, _lock)
    # SUPERADMIN "block collection from online" (BVI-retirement): a product that
    # belongs to AT LEAST ONE online_sync_blocked collection is a HARD block --
    # it must NEVER be created/updated on Shopify regardless of its other
    # (unblocked) collection memberships (a brand ban wins). This single guard is
    # the ONE chokepoint, so BOTH the per-product push route AND the /all-pending
    # product sweep are covered. FAIL-CLOSED: the strict classifier returns None
    # (UNKNOWN) on a block-config read error and we SKIP the push (never a false
    # 'clean' that ships a banned product -- finding #18). Delisting an
    # already-synced blocked product is done separately by push_product_delist,
    # which is NOT gated here (it IS the block action).
    if blocked is None:
        try:
            from .online_block import is_blocked_from_online_strict

            blocked = is_blocked_from_online_strict(product, db)
        except Exception:  # noqa: BLE001 -- classifier must never break a push
            blocked = None
    if blocked is None:
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            error="block status unverifiable (block-config read error) -- "
            "push skipped (fail-closed)",
            reason="block_status_unverifiable",
        )
    if blocked:
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            error="blocked from online (member of an online_sync_blocked collection)",
            reason="online_sync_blocked",
        )
    variants = variants or []
    ecom = product.get("ecom") or {}

    # THE PHOTO RULE (owner ruling 2026-08-25): "Refuse -- no photo, no
    # publish." A listing with a name, a price and an empty grey box is worse
    # for the brand than absence. Since a press now PUBLISHES (see
    # build_product_input), a push with no photograph is refused outright,
    # BEFORE the dark/live branch -- so there is no path, dry-run or live, on
    # which a photo-less product reaches Shopify at all. The row is NOT
    # de-queued (nothing here writes back), so adding a photo and pressing
    # again ships it.
    photos = product_photo_urls(product)
    if not photos:
        return PushResult(
            mode=MODE_BLOCKED,
            entity="product",
            action="skip",
            target_id=pid,
            ok=False,
            shopify_id=ecom.get("shopify_product_id"),
            error="refused: no photograph -- a product with no photograph is "
            "never published to the storefront",
            reason="no_photo",
        )

    existing_gid = ecom.get("shopify_product_id")
    payload = build_product_input(product, variants)
    # Attribute -> metafield side channel (owner 2026-07-05): planned in the
    # dry-run, upserted via metafieldsSet after a LIVE product write succeeds.
    metafields = build_product_metafields(product)
    action = "update" if existing_gid else "create"
    # full_reseed: touch EVERY variant row (CREATE always does; an UPDATE only
    # when the owner explicitly opted in). repair_only: an UPDATE that is NOT
    # a full reseed, but at least one row/the pseudo-variant has never been
    # seeded -- restricted to ONLY that unseeded subset (#955 fix-round:
    # _needs_repair is ROW-aware so a partially-harvested product keeps
    # getting a chance to finish, and build_repair_seed_rows/repair_only=True
    # keep an already-seeded row from ever being re-touched by the repair).
    full_reseed = action == "create" or price_on_update_enabled()
    repair_only = (not full_reseed) and _needs_repair(product, variants)

    live, reason = _live_or_reason(db)
    if not live:
        # Variant price/barcode plan rides on the dry-run too (owner priority:
        # a price change must be visibly part of the push plan). Covers the
        # product-level pseudo-variant of a no-variant-row product (OS-016).
        vp_plan = None
        vp_variants = _variants_for_price_push(product, variants)
        if vp_variants:
            vp_rows, vp_skipped = build_variant_price_inputs(product, vp_variants)
            vp_plan = {"variants": vp_rows, "skipped_no_gid_or_price": vp_skipped}
        # The CREATE-side seeding plan (price + SKU for the variants Shopify will
        # mint) rides on the dry-run too, so the owner can SEE the money before
        # anything goes live. On an update it appears when the opt-in flag is on
        # (every row) OR when the product still needs repair (only the
        # still-unseeded row(s)) -- mirroring exactly what the LIVE branch
        # below would actually do.
        seed_plan = None
        if full_reseed:
            seed_plan = plan_variant_seed(product, variants)
        elif repair_only:
            seed_plan = plan_variant_seed(product, variants, repair_only=True)
        return PushResult(
            mode=MODE_SIMULATED,
            entity="product",
            action=action,
            target_id=pid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
            metafields=metafields or None,
            variant_prices=vp_plan,
            variants_seeded=seed_plan,
        )

    query = _PRODUCT_UPDATE if existing_gid else _PRODUCT_CREATE
    field_name = "productUpdate" if existing_gid else "productCreate"
    try:
        body = await _graphql(db, query, {"input": payload})
        err = _user_errors(body, field_name)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action=action,
                target_id=pid,
                ok=False,
                payload=payload,
                error=err,
            )
        prod = ((body.get("data") or {}).get(field_name) or {}).get("product") or {}
        new_gid = prod.get("id") or existing_gid
        if new_gid and pid:
            _writeback_product(db, pid, new_gid)
        # Metafields ride AFTER the product write so the gid always exists.
        # Fail-soft: their errors are reported on the result, never flip ok.
        mf_summary = None
        if metafields and new_gid:
            mf_summary = await _set_product_metafields(db, new_gid, metafields)
        # VARIANT SEEDING -- the price-0.00 / no-SKU fix. ProductInput carries
        # neither, so on a CREATE the variants Shopify just minted are priced +
        # SKU'd here and their gids written back -- BOTH the ProductVariant gid
        # (the price push's handle) and the InventoryItem gid (the oversell-
        # guard stock write-back's target: catalog_variants.
        # shopify_inventory_item_id per row, ecom.shopify_inventory_item_id for
        # a no-variant-row product). On an UPDATE the FULL reseed only runs
        # when the owner opts in (SHOPIFY_PUSH_PRICE_ON_UPDATE); otherwise a
        # ROW-AWARE repair-only pass runs whenever ANY row still lacks a gid
        # (_needs_repair), restricted to ONLY that unseeded subset
        # (repair_only=True -> build_repair_seed_rows) so an already-seeded
        # row can never be re-priced/re-touched by the repair (#955
        # fix-round: the previous product-wide any-gid check permanently
        # masked a partially-harvested product's remaining stranded row from
        # ever being retried). A fully-seeded product is left untouched, so
        # the ~4,400 already-live products are never silently re-priced.
        # Idempotent + fail-soft.
        seed_summary = None
        seeded = False
        if new_gid and (full_reseed or repair_only):
            variant_nodes = ((prod.get("variants") or {}).get("nodes")) or []
            seed_summary = await _seed_variants_after_write(
                db, product, variants, new_gid, variant_nodes,
                repair_only=repair_only,
            )
            seeded = seed_summary is not None
            if seed_summary and seed_summary.get("default_variant_gid") and pid:
                _writeback_product(
                    db,
                    pid,
                    new_gid,
                    variant_gid=seed_summary["default_variant_gid"],
                    # Only ever set for the product-level pseudo-variant (the
                    # product has NO catalog_variants rows) -- the resolver's
                    # documented ecom fallback. None otherwise, which leaves
                    # ecom untouched (set-only).
                    inventory_item_gid=seed_summary.get(
                        "product_level_inventory_item_gid"
                    ),
                )
        # THE PHOTOGRAPH, IN THIS SAME PRESS. "Has a photo in IMS" and "has a
        # photo on Shopify" are different questions, and only the second one
        # protects the storefront -- photographs used to push on a SEPARATE,
        # LATER press (push_image over the design queue), so a product could go
        # visible before its photo arrived. The refusal above proved IMS has a
        # photograph; this puts it on Shopify before anything is published.
        #
        # Only when the Shopify product carries NO media yet (read straight off
        # the create/update response's media selection -- no extra call): that
        # covers the create, repairs a product an OLDER press put up bare, and
        # can never pile a duplicate copy onto an already-photographed listing.
        photo_summary = None
        existing_media = ((prod.get("media") or {}).get("nodes")) or []
        if new_gid and not existing_media:
            photo_summary = await _attach_product_photos(db, new_gid, photos)
        photo_on_shopify = bool(existing_media) or bool(
            (photo_summary or {}).get("attached")
        )
        # SALES-CHANNEL PUBLISH -- the third shut door. An ACTIVE product
        # published to NO channel is invisible on bettervision.in. This used to
        # be gated behind SHOPIFY_PUBLISH_ON_CREATE (default OFF) AND restricted
        # to a CREATE, so it effectively never ran -- and re-pressing one of the
        # rows already mapped to Shopify (all of which got there as DRAFTs) could
        # never make it visible. Owner ruling 2026-08-25 "one press, goes live":
        # it now runs on every product push, create or update. publishablePublish
        # is idempotent, so re-publishing an already-published product is a no-op.
        #
        # PUBLISH PRECONDITION (adversarial-panel must-fix 1, still enforced):
        # publishing is WITHHELD unless the price is provably right, because
        # seeding is fail-SOFT (a bulk-update userError or a priceless product
        # still leaves ok=True) and a 0.00 listing is worse than no listing.
        #   * a press that SEEDED (create, or an update that repaired/reseeded):
        #     the seeding must have run clean AND priced at least one row.
        #   * a press that needed NO seeding: every variant already carries the
        #     gid an earlier successful seed wrote, so its price is already on
        #     Shopify -- but IMS must still hold a positive price for every row.
        pub_summary = None
        if new_gid and payload.get("status") == "ACTIVE":
            if seed_summary is not None:
                priced_ok = (
                    not seed_summary.get("errors")
                    and int(seed_summary.get("priced_rows") or 0) > 0
                )
            elif full_reseed or repair_only:
                # Seeding was due but produced nothing to seed -- no price and
                # no SKU anywhere. Never publish that.
                priced_ok = False
            else:
                priced_ok = _has_publishable_price(product, variants)
            if priced_ok and photo_on_shopify:
                pub_summary = await _publish_to_online_store(db, new_gid)
                if pub_summary.get("published") and pid:
                    # IMS must agree with the storefront (see _writeback_product
                    # `status`): the DRAFT/PUBLISHED cards and every
                    # storefront-visibility helper read ecom.status.
                    _writeback_product(db, pid, new_gid, status="PUBLISHED")
            elif not photo_on_shopify:
                # The attach is fail-soft, so a media error must not leave the
                # product VISIBLE without its photograph -- that is exactly the
                # grey box the rule exists to prevent.
                pub_summary = {
                    "published": False,
                    "error": "publish withheld: the photograph did not reach Shopify",
                }
            else:
                pub_summary = {
                    "published": False,
                    "error": "publish withheld: variant unpriced or seeding failed",
                }
        # The press reached Shopify but the product is NOT visible. Leave it in
        # the queue so pressing again retries it once the price / photograph is
        # fixed -- see _requeue_unpublished for why this is not ping-pong.
        #
        # AND SAY SO. "One press, goes live" makes visibility the definition of
        # success, so a press whose publish was withheld did NOT do what it was
        # pressed for and must never come back ok. Reporting ok=True here was
        # the owner's original bug one layer down: with the publication
        # unresolvable a sweep answered "pushed: 5, failed: 0" over five
        # invisible products and the toast was green. The withholding gets its
        # OWN reason (never `failed`, which would read as a Shopify breakage)
        # so the sweep buckets and shows it exactly like refused_no_photo.
        published_ok = pub_summary is None or bool(pub_summary.get("published"))
        if not published_ok and pid:
            _requeue_unpublished(db, pid)
        # Variant price/barcode push rides after the product write too (same
        # fail-soft side-channel contract: an error is reported on the result,
        # never flips the product push's ok). push_variant_prices never raises.
        # Skipped when the seeding step above already wrote these exact prices.
        # Also covers a no-variant-row product's default variant (via the
        # stored ecom.shopify_variant_id pseudo-variant) so a queued price
        # change is actually CARRIED by the update push (OS-016).
        vp_summary = None
        if _variants_for_price_push(product, variants) and new_gid and not seeded:
            vp_product = dict(product)
            vp_ecom = dict(vp_product.get("ecom") or {})
            vp_ecom["shopify_product_id"] = new_gid
            vp_product["ecom"] = vp_ecom
            vp_res = await push_variant_prices(db, vp_product, variants)
            vp_summary = {
                "ok": vp_res.ok,
                "action": vp_res.action,
                "pushed": len((vp_res.payload or {}).get("variants") or []),
                "skipped_no_gid_or_price": (vp_res.payload or {}).get(
                    "skipped_no_gid_or_price", 0
                ),
                "error": vp_res.error,
            }
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action=action,
            target_id=pid,
            ok=published_ok,
            shopify_id=new_gid,
            payload=payload,
            error=(
                None
                if published_ok
                else ((pub_summary or {}).get("error") or "publish withheld")
            ),
            reason=None if published_ok else "publish_withheld",
            metafields=mf_summary,
            variant_prices=vp_summary,
            variants_seeded=seed_summary,
            publication=pub_summary,
            photos=photo_summary,
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action=action,
            target_id=pid,
            ok=False,
            payload=payload,
            error=str(e),
        )


async def push_product_delist(db, product: Dict[str, Any]) -> PushResult:
    """DELIST a product from the Shopify storefront: set its Shopify status to
    DRAFT (unpublished / not sellable). Used by the SUPERADMIN "block collection
    from online" cutover to take an already-synced, now-blocked product OFF the
    storefront.

    REVERSIBLE by design: the Shopify product is NEVER deleted and its gid is
    KEPT, so putting it back is a normal push (and can never mint a duplicate
    listing). Unlike push_product this is NOT gated by
    is_blocked_from_online (it IS the block action). Obeys the same three dark
    gates: SIMULATED plan when dark, LIVE productUpdate only behind the gates.
    Only acts when the product already carries a Shopify gid (else a clean noop --
    nothing to delist). Never raises.

    AFTER A SUCCESSFUL LIVE DELIST the IMS row is brought into line with the
    storefront (owner ruling 2026-08-25 -- take-down is the reversibility that
    makes one-press publishing survivable):
      * ecom.status -> DRAFT. ecom.status is READ in six places (the Online
        Store screen's DRAFT/PUBLISHED cards, the storefront-visibility
        helpers); leaving it PUBLISHED would have IMS insisting a product is
        on a storefront it was just pulled from. This does NOT re-shut the
        publish door: build_product_input maps everything except ARCHIVED to
        ACTIVE, so pressing publish again puts it straight back.
      * ecom.locally_modified -> False. Without this a taken-down product
        that happened to be dirty would be RESURRECTED by the very next
        sweep, seconds later. Taking one down has to stick until a human
        asks for it back.
    Both live in HERE rather than in the caller because both callers -- the
    block cutover and the take-down button -- need the same truth: this row
    is off the storefront.

    The gid is deliberately re-written unchanged: _writeback_product is
    set-only for the mapping, so the take-down can never lose the Shopify id
    (losing it would make the next push CREATE A DUPLICATE live product)."""
    pid = product.get("id") or product.get("product_id")
    ecom = product.get("ecom") or {}
    existing_gid = ecom.get("shopify_product_id")
    if not existing_gid:
        # Not on Shopify -> nothing to take down (a clean no-op, not an error).
        return PushResult(
            mode=MODE_SIMULATED,
            entity="product",
            action="noop",
            target_id=pid,
            ok=True,
            reason="not on Shopify -- nothing to delist",
        )
    payload: Dict[str, Any] = {
        "id": _as_shopify_gid(existing_gid, "Product"),
        "status": "DRAFT",
    }

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="product",
            action="delist",
            target_id=pid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    try:
        body = await _graphql(db, _PRODUCT_UPDATE, {"input": payload})
        err = _user_errors(body, "productUpdate")
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action="delist",
                target_id=pid,
                ok=False,
                shopify_id=existing_gid,
                payload=payload,
                error=err,
            )
        if pid:
            _writeback_product(db, pid, existing_gid, status="DRAFT")
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action="delist",
            target_id=pid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action="delist",
            target_id=pid,
            ok=False,
            shopify_id=existing_gid,
            payload=payload,
            error=str(e),
        )


async def push_variant_prices(
    db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None
) -> PushResult:
    """Push variant price / compareAtPrice / barcode for ONE product's mapped
    variants via productVariantsBulkUpdate (owner priority: "change MRP in IMS
    -> website updates").

    UPDATE-only by design: a variant with no stored shopify_variant_id is
    SKIPPED (counted in the payload) -- variants get their gid when the
    product's variants first sync; creating variants here would fork that
    ownership. DARK by default -> SIMULATED plan with the exact
    ProductVariantsBulkInput rows and NO network call; LIVE only behind the
    same three gates. Never raises (fail-soft PushResult contract)."""
    pid = product.get("id") or product.get("product_id")
    # Hub Phase 5 push-lock, FIRST gate (fail-closed): a locked brand's prices
    # must never reach Shopify either.
    _lock = push_lock_reason(db, "product", product)
    if _lock:
        return _blocked_result("variant-prices", pid, _lock)

    ecom = product.get("ecom") or {}
    raw_gid = ecom.get("shopify_product_id")
    product_gid = _as_shopify_gid(raw_gid, "Product") if raw_gid else None
    # No catalog_variants rows -> the product-level pseudo-variant (the stored
    # default-variant gid), so a single-variant product's price change can
    # still be re-pushed (OS-016 / OS-017). [] when no gid -> clean noop.
    rows, skipped = build_variant_price_inputs(
        product, _variants_for_price_push(product, variants)
    )
    payload: Dict[str, Any] = {
        "productId": product_gid,
        "variants": rows,
        "skipped_no_gid_or_price": skipped,
    }
    action = "update" if rows else "noop"

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="variant-prices",
            action=action,
            target_id=pid,
            ok=True,
            shopify_id=product_gid,
            payload=payload,
            reason=reason,
        )

    if not rows:
        # Nothing mapped (or nothing priced) -> a clean no-op, not an error.
        return PushResult(
            mode=MODE_LIVE,
            entity="variant-prices",
            action="noop",
            target_id=pid,
            ok=True,
            shopify_id=product_gid,
            payload=payload,
        )
    if not product_gid:
        return PushResult(
            mode=MODE_LIVE,
            entity="variant-prices",
            action="skip",
            target_id=pid,
            ok=False,
            payload=payload,
            error="parent product not on Shopify yet (push the product first)",
        )
    try:
        for i in range(0, len(rows), _VARIANTS_PER_CALL):
            chunk = rows[i : i + _VARIANTS_PER_CALL]
            body = await _graphql(
                db,
                _VARIANTS_BULK_UPDATE,
                {"productId": product_gid, "variants": chunk},
            )
            err = _user_errors(body, "productVariantsBulkUpdate")
            if err:
                return PushResult(
                    mode=MODE_LIVE,
                    entity="variant-prices",
                    action=action,
                    target_id=pid,
                    ok=False,
                    shopify_id=product_gid,
                    payload=payload,
                    error=err,
                )
        return PushResult(
            mode=MODE_LIVE,
            entity="variant-prices",
            action=action,
            target_id=pid,
            ok=True,
            shopify_id=product_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        return PushResult(
            mode=MODE_LIVE,
            entity="variant-prices",
            action=action,
            target_id=pid,
            ok=False,
            shopify_id=product_gid,
            payload=payload,
            error=str(e),
        )


async def push_collection(db, collection: Dict[str, Any]) -> PushResult:
    """Push an ecom_collections doc to Shopify (collectionCreate / collectionUpdate,
    + smart ruleSet when SMART, + manual membership when CUSTOM). DARK by default;
    LIVE behind the gates with gid write-back. Never raises.

    MEMBERSHIP (parity fix): a CUSTOM collection's manual member list is NOT part
    of CollectionInput, so after the collection upsert its members are attached
    via collectionAddProducts (mirrors BVI's addProductsToCollection). SMART
    membership is derived by Shopify from the ruleSet -- no add step. Members
    whose product isn't on Shopify yet are skipped (they join on the product's
    first sync). The membership push is a fail-soft side channel: an error is
    reported in `membership` but never flips the collection push's ok."""
    cid = collection.get("collection_id")
    # Hub Phase 5: push-lock first -- a locked collection handle is NEVER pushed.
    _lock = push_lock_reason(db, "collection", collection)
    if _lock:
        return _blocked_result("collection", cid, _lock)
    existing_gid = collection.get("shopify_collection_id")
    payload = build_collection_input(collection)
    action = "update" if existing_gid else "create"
    # CUSTOM manual membership plan (empty for SMART).
    member_gids, member_skipped = _member_product_gids(db, collection)
    is_custom = str(collection.get("collection_type") or "CUSTOM").upper() == "CUSTOM"

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="collection",
            action=action,
            target_id=cid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
            membership=(
                {"product_ids": member_gids, "skipped_not_on_shopify": member_skipped}
                if is_custom
                else None
            ),
        )

    query = _COLLECTION_UPDATE if existing_gid else _COLLECTION_CREATE
    field_name = "collectionUpdate" if existing_gid else "collectionCreate"
    try:
        body = await _graphql(db, query, {"input": payload})
        err = _user_errors(body, field_name)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="collection",
                action=action,
                target_id=cid,
                ok=False,
                payload=payload,
                error=err,
            )
        coll_obj = ((body.get("data") or {}).get(field_name) or {}).get(
            "collection"
        ) or {}
        new_gid = coll_obj.get("id") or existing_gid
        if new_gid and cid:
            _writeback_simple(
                db,
                "ecom_collections",
                "collection_id",
                cid,
                "shopify_collection_id",
                new_gid,
            )
        # CUSTOM manual membership rides AFTER the collection upsert (the gid must
        # exist to attach products to). Fail-soft side channel: reported in
        # `membership`, never flips the collection push's ok. push never raises.
        membership_summary = None
        if is_custom and new_gid and member_gids:
            mres = await _push_collection_membership(db, new_gid, member_gids)
            membership_summary = {**mres, "skipped_not_on_shopify": member_skipped}
        elif is_custom:
            membership_summary = {
                "added": 0,
                "errors": [],
                "skipped_not_on_shopify": member_skipped,
            }
        return PushResult(
            mode=MODE_LIVE,
            entity="collection",
            action=action,
            target_id=cid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
            membership=membership_summary,
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="collection",
            action=action,
            target_id=cid,
            ok=False,
            payload=payload,
            error=str(e),
        )


async def push_menu(db, menu: Dict[str, Any]) -> PushResult:
    """Push an ecom_menus doc (the Online Store nav / mega-menu) to Shopify
    (menuCreate / menuUpdate) mapping the nested item tree. DARK by default; LIVE
    behind the gates with gid write-back. Never raises."""
    mid = menu.get("menu_id")
    existing_gid = menu.get("shopify_menu_id")
    items = build_menu_items(menu.get("items") or [])
    title = menu.get("title") or menu.get("handle")
    handle = menu.get("handle")
    action = "update" if existing_gid else "create"
    payload: Dict[str, Any] = {"title": title, "handle": handle, "items": items}
    if existing_gid:
        payload["id"] = _as_shopify_gid(existing_gid, "Menu")

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="menu",
            action=action,
            target_id=mid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    if existing_gid:
        query, field_name = _MENU_UPDATE, "menuUpdate"
        variables = {
            "id": _as_shopify_gid(existing_gid, "Menu"),
            "title": title,
            "handle": handle,
            "items": items,
        }
    else:
        query, field_name = _MENU_CREATE, "menuCreate"
        variables = {"title": title, "handle": handle, "items": items}
    try:
        body = await _graphql(db, query, variables)
        err = _user_errors(body, field_name)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="menu",
                action=action,
                target_id=mid,
                ok=False,
                payload=payload,
                error=err,
            )
        menu_obj = ((body.get("data") or {}).get(field_name) or {}).get("menu") or {}
        new_gid = menu_obj.get("id") or existing_gid
        if new_gid and mid:
            _writeback_simple(
                db, "ecom_menus", "menu_id", mid, "shopify_menu_id", new_gid
            )
        return PushResult(
            mode=MODE_LIVE,
            entity="menu",
            action=action,
            target_id=mid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="menu",
            action=action,
            target_id=mid,
            ok=False,
            payload=payload,
            error=str(e),
        )


async def push_image(db, image: Dict[str, Any]) -> PushResult:
    """Push ONE APPROVED product image to Shopify (productCreateMedia) onto its
    parent product. DARK by default; LIVE behind the gates with the returned
    MediaImage gid written back to shopify_image_id. Never raises.

    GUARD: only an APPROVED image is push-eligible (the design queue gate).
    Anything else returns ok=False action=skip (Fail Loudly) without a network
    call. The parent product MUST already be on Shopify (ecom.shopify_product_id)
    -- without it there is nothing to attach the media to; that is a skip too."""
    iid = image.get("image_id")
    existing_gid = image.get("shopify_image_id")

    # Hub Phase 5 push-lock (defense-in-depth, FIRST gate): an image attaches to
    # its parent product, so a push-locked brand's image must NEVER reach Shopify
    # either. push_product is already blocked for a locked brand (so the parent is
    # normally never on Shopify), but this closes the legacy "product was on
    # Shopify before its brand got locked" gap. Fail-CLOSED on a real lock match.
    _parent = _resolve_product_doc(db, image.get("product_id"))
    if _parent is not None:
        _img_lock = push_lock_reason(db, "product", _parent)
        if _img_lock:
            return _blocked_result("image", iid, _img_lock)

    if str(image.get("status") or "").upper() != "APPROVED":
        return PushResult(
            mode=MODE_SIMULATED,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload={"status": image.get("status")},
            error="only APPROVED images are push-eligible",
        )

    # Resolve the parent product's Shopify gid (media attaches to a product).
    product_gid = _resolve_product_gid(db, image.get("product_id"))
    media = build_media_inputs([image])
    payload: Dict[str, Any] = {"productId": product_gid, "media": media}
    action = "update" if existing_gid else "create"

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="image",
            action=action,
            target_id=iid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    if not product_gid:
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload=payload,
            error="parent product not on Shopify yet (push the product first)",
        )
    if not media:
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload=payload,
            error="no image url to push",
        )
    try:
        body = await _graphql(
            db, _PRODUCT_CREATE_MEDIA, {"productId": product_gid, "media": media}
        )
        err = _user_errors_media(body)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="image",
                action=action,
                target_id=iid,
                ok=False,
                payload=payload,
                error=err,
            )
        media_nodes = ((body.get("data") or {}).get("productCreateMedia") or {}).get(
            "media"
        ) or []
        new_gid = (media_nodes[0].get("id") if media_nodes else None) or existing_gid
        # Persist the MediaImage gid for idempotency. _writeback_image now takes
        # the WHOLE image doc so it can locate the row even when image_id is null
        # (the BVI-migrated docs) via the natural key (product_id + url). If it
        # STILL cannot persist, the media WAS created on Shopify but we have no
        # way to record it -> Fail Loudly (ok=False) instead of a silent success,
        # because a clean-looking ok=True on an un-recorded create is exactly what
        # let a re-run duplicate media. shopify_id is still returned so the audit
        # row captures the orphaned gid for manual reconcile.
        if new_gid:
            persisted = _writeback_image(db, image, new_gid)
            if not persisted:
                return PushResult(
                    mode=MODE_LIVE,
                    entity="image",
                    action=action,
                    target_id=iid,
                    ok=False,
                    shopify_id=new_gid,
                    payload=payload,
                    error=(
                        "media attached on Shopify (%s) but shopify_image_id "
                        "write-back failed: no stable image key (image_id or "
                        "product_id+url) to persist it -- manual reconcile "
                        "required to avoid a duplicate on re-push" % new_gid
                    ),
                )
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action=action,
            target_id=iid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action=action,
            target_id=iid,
            ok=False,
            payload=payload,
            error=str(e),
        )


def _user_errors_media(body: Dict[str, Any]) -> Optional[str]:
    """productCreateMedia uses `mediaUserErrors` (not `userErrors`)."""
    if not isinstance(body, dict):
        return "malformed graphql response"
    if body.get("errors"):
        return f"graphql errors: {str(body['errors'])[:300]}"
    field_obj = (body.get("data") or {}).get("productCreateMedia") or {}
    ue = field_obj.get("mediaUserErrors") or []
    if ue:
        return f"mediaUserErrors: {str(ue)[:300]}"
    return None


def _resolve_product_doc(db, product_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Load the parent catalog_products doc (for the push-lock brand check on an
    image). Fail-soft -> None."""
    if not product_id or db is None:
        return None
    try:
        return db["catalog_products"].find_one({"id": product_id})
    except Exception:  # noqa: BLE001
        return None


def _resolve_product_gid(db, product_id: Optional[str]) -> Optional[str]:
    """Look up the parent catalog_products' ecom.shopify_product_id (the gid the
    image media attaches to). Fail-soft -> None."""
    if not product_id:
        return None
    try:
        doc = db["catalog_products"].find_one({"id": product_id})
        if doc is None:
            return None
        gid = (doc.get("ecom") or {}).get("shopify_product_id")
        return _as_shopify_gid(gid, "Product") if gid else None
    except Exception:  # noqa: BLE001
        return None


def _image_writeback_filter(image: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The Mongo filter that uniquely locates this image doc for a write-back.

    Prefers the primary key `image_id`. When it is missing/null (the BVI-migrated
    docs are stored with image_id=None) it falls back to the documented natural
    key product_id + url (ProductImageRepository: "Idempotent keys (never _id):
    image_id | product_id | variant_id"). Returns None when NEITHER is available
    -- there is then no safe way to target exactly one row, so the caller must
    fail loudly rather than write blindly."""
    iid = image.get("image_id")
    if iid:
        return {"image_id": iid}
    pid = image.get("product_id")
    url = image.get("url")
    if pid and url:
        return {"product_id": pid, "url": url}
    return None


def _writeback_image(db, image: Dict[str, Any], shopify_id: str) -> bool:
    """Persist shopify_image_id on the product_images doc. Returns True iff a row
    was actually located + written, False otherwise (no usable key, or a fail-soft
    error). The gid presence is the idempotency key (the image has no
    locally_modified flag), so a reliable write-back is what stops a re-push from
    duplicating media.

    Takes the WHOLE image doc (not just an id) so it can locate the row via the
    natural key when image_id is null -- the exact condition that made the
    BVI-migrated docs silently skip their write-back before this fix."""
    filt = _image_writeback_filter(image)
    if filt is None:
        return False
    try:
        res = db["product_images"].update_one(
            filt,
            {"$set": {"shopify_image_id": shopify_id, "updated_at": _now()}},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[SHOPIFY_PUSH] image write-back failed {filt}: {e}"
        )
        return False
    # matched_count on real pymongo; MockCollection exposes modified_count only.
    touched = getattr(res, "matched_count", None)
    if touched is None:
        touched = getattr(res, "modified_count", 0)
    return bool(touched)


# ===========================================================================
# WEBHOOK SUBSCRIPTION REGISTRATION (Phase-6 cutover: Shopify must call IMS)
# ===========================================================================
# Today orders flow Shopify -> BVI. At the baton cutover Shopify must instead
# POST signed webhooks at IMS's already-live receiver POST /api/v1/webhooks/
# shopify (routers/webhooks.py: HMAC-verified against the `integrations` doc's
# shopify webhook_secret, persisted to webhook_inbox, drained by NEXUS). This
# registrar creates the missing webhookSubscriptions via the Admin API.
#
# NOTE for verification: webhooks created via the Admin API are signed by
# Shopify with the CUSTOM APP's API SECRET KEY (the app whose access token we
# push with) -- NOT the "Notifications" shared secret shown in the Shopify
# admin UI. The owner must store that API secret key as `webhook_secret` on
# the shopify `integrations` config or every delivery will 401.

_WEBHOOK_SUBSCRIPTIONS_QUERY = """
query imsWebhookSubscriptions($first: Int!, $after: String) {
  webhookSubscriptions(first: $first, after: $after) {
    edges {
      node {
        id
        topic
        endpoint {
          __typename
          ... on WebhookHttpEndpoint { callbackUrl }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# The webhook subscription list is paginated: the custom app can accumulate >100
# subscriptions (BVI history + retries), so a single first:100 read would miss
# subs on later pages -> a sub already at IMS's URL would look 'missing' and a
# BVI-pointing conflict would go unsurfaced (finding #19). Walk every page (up to
# this fail-soft cap) before deciding create/skip/delete.
_WEBHOOK_PAGE_SIZE = 100
_WEBHOOK_MAX_PAGES = 10

_WEBHOOK_SUBSCRIPTION_CREATE = """
mutation imsWebhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
    webhookSubscription { id topic }
    userErrors { field message }
  }
}
"""

# Phase-6 cutover: delete a conflicting subscription (e.g. one still pointing at
# BVI) so Shopify stops double-delivering the same topic once IMS is registered.
_WEBHOOK_SUBSCRIPTION_DELETE = """
mutation imsWebhookSubscriptionDelete($id: ID!) {
  webhookSubscriptionDelete(id: $id) {
    deletedWebhookSubscriptionId
    userErrors { field message }
  }
}
"""

# The receiver route the subscriptions point at (mounted under /api/v1).
_WEBHOOK_RECEIVER_PATH = "/api/v1/webhooks/shopify"

# BVI-retirement cutover topic set: every Shopify webhook IMS must receive once
# BVI is retired -- the order lifecycle (count-once invoice + status sync),
# refunds (GST credit note + restock), fulfilments (shipped/tracking), and
# customers (CRM upsert). register_webhooks defaults to THIS set so a single
# apply=true registers everything the receiver now handles.
CUTOVER_WEBHOOK_TOPICS = [
    "orders/create",
    "orders/paid",
    "orders/updated",
    "orders/cancelled",
    "orders/fulfilled",
    "orders/partially_fulfilled",
    "refunds/create",
    "fulfillments/create",
    "fulfillments/update",
    "customers/create",
    "customers/update",
]


def _topic_enum(topic: str) -> str:
    """'orders/create' -> 'ORDERS_CREATE' (Shopify WebhookSubscriptionTopic).
    Already-enum input ('ORDERS_CREATE') passes through unchanged."""
    return str(topic or "").strip().replace("/", "_").replace(".", "_").upper()


async def delete_webhook_subscription(db, subscription_id: str) -> Dict[str, Any]:
    """Delete ONE Shopify webhookSubscription by gid (Phase-6 cutover: drop a
    conflicting subscription still pointing at BVI so a topic stops
    double-delivering). DARK by default -> SIMULATED, no network call; LIVE only
    behind the same three push gates. Fail-soft: returns a structured dict, never
    raises."""
    result: Dict[str, Any] = {
        "ok": True,
        "mode": MODE_SIMULATED,
        "id": subscription_id,
        "deleted": None,
        "errors": [],
        "reason": None,
    }
    if not subscription_id:
        result["ok"] = False
        result["errors"].append("no subscription id")
        return result

    live, reason = _live_or_reason(db)
    result["reason"] = reason
    result["mode"] = MODE_LIVE if live else MODE_SIMULATED
    if not live:
        result["note"] = "SIMULATED: push gates closed -- no Shopify call made"
        return result

    try:
        body = await _graphql(db, _WEBHOOK_SUBSCRIPTION_DELETE, {"id": subscription_id})
        err = _user_errors(body, "webhookSubscriptionDelete")
        if err:
            result["ok"] = False
            result["errors"].append(err)
            return result
        deleted = (
            (body.get("data") or {}).get("webhookSubscriptionDelete") or {}
        ).get("deletedWebhookSubscriptionId")
        result["deleted"] = deleted
        return result
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        result["ok"] = False
        result["errors"].append(str(e))
        return result


async def register_webhooks(
    db,
    callback_base_url: str,
    topics: Optional[List[str]] = None,
    apply: bool = False,
    delete_conflicts: bool = False,
) -> Dict[str, Any]:
    """Ensure Shopify webhookSubscriptions exist for `topics`, pointing at
    {callback_base_url}/api/v1/webhooks/shopify. Fail-soft: returns a structured
    dict, never raises.

    `topics` DEFAULTS to the full BVI-retirement cutover set
    (CUTOVER_WEBHOOK_TOPICS: order lifecycle + refunds/create +
    fulfillments/create,update + customers/create,update) so a single apply=true
    registers everything the receiver now handles.

    DRY-RUN by default (apply=False): reports what WOULD be registered. When
    the three push gates are LIVE the dry-run also QUERIES the existing
    subscriptions (a read); when DARK it makes NO network call at all.
    Mutations happen ONLY when apply=True AND the gates are LIVE, and only for
    topics not already subscribed at this exact callback URL (idempotent).

    `delete_conflicts` (default False): when True AND apply=True AND LIVE, also
    DELETE every surfaced conflict (a requested topic subscribed at a DIFFERENT
    callback URL -- e.g. still pointing at BVI) so the cutover leaves exactly one
    delivery per topic. Left False, conflicts are only SURFACED, never removed."""
    topic_enums = [
        _topic_enum(t) for t in (topics or CUTOVER_WEBHOOK_TOPICS) if _topic_enum(t)
    ]
    base = str(callback_base_url or "").strip().rstrip("/")
    callback_url = base + _WEBHOOK_RECEIVER_PATH

    live, reason = _live_or_reason(db)
    result: Dict[str, Any] = {
        "ok": True,
        "mode": MODE_LIVE if live else MODE_SIMULATED,
        "applied": False,
        "callback_url": callback_url,
        "topics": topic_enums,
        "existing": [],
        "already_registered": [],
        "missing": list(topic_enums),
        "conflicts": [],
        "created": [],
        "deleted_conflicts": [],
        "errors": [],
        "reason": reason,
    }
    if not base.startswith("https://"):
        result["ok"] = False
        result["errors"].append(
            "callback_base_url must be https:// (Shopify rejects non-https "
            "webhook endpoints)"
        )
        return result
    if not topic_enums:
        result["ok"] = False
        result["errors"].append("no topics requested")
        return result

    if not live:
        # DARK: no network at all (not even the read). The plan lists every
        # requested topic as missing; existing subscriptions are unknown.
        result["note"] = (
            "SIMULATED: push gates closed -- no Shopify call made; existing "
            "subscriptions unknown, every requested topic listed as missing."
        )
        return result

    try:
        # PAGINATE the subscription list: walk every page (until hasNextPage is
        # false, capped fail-soft at _WEBHOOK_MAX_PAGES) so a sub beyond page 1 is
        # never missed (finding #19). A missed sub would either force a duplicate
        # create (userError) or leave a BVI conflict unsurfaced/undeleted.
        edges: List[Dict[str, Any]] = []
        after: Optional[str] = None
        for _page in range(_WEBHOOK_MAX_PAGES):
            body = await _graphql(
                db,
                _WEBHOOK_SUBSCRIPTIONS_QUERY,
                {"first": _WEBHOOK_PAGE_SIZE, "after": after},
            )
            if not isinstance(body, dict) or body.get("errors"):
                result["ok"] = False
                result["errors"].append(
                    f"webhookSubscriptions query failed: {str((body or {}).get('errors'))[:300]}"
                )
                return result
            conn = ((body.get("data") or {}).get("webhookSubscriptions") or {})
            edges.extend(conn.get("edges") or [])
            page_info = conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
            if not after:
                break
        existing: List[Dict[str, Any]] = []
        for e in edges:
            node = (e or {}).get("node") or {}
            endpoint = node.get("endpoint") or {}
            existing.append(
                {
                    "id": node.get("id"),
                    "topic": node.get("topic"),
                    "callback_url": endpoint.get("callbackUrl"),
                }
            )
        result["existing"] = existing
        already = {
            x["topic"]
            for x in existing
            if x.get("topic") in topic_enums
            and x.get("callback_url") == callback_url
        }
        # Same topic subscribed at a DIFFERENT URL (e.g. still pointing at
        # BVI): surfaced so the owner sees the double-delivery risk; we still
        # treat OUR url as missing.
        result["conflicts"] = [
            x
            for x in existing
            if x.get("topic") in topic_enums
            and x.get("callback_url")
            and x.get("callback_url") != callback_url
        ]
        result["already_registered"] = sorted(already)
        result["missing"] = [t for t in topic_enums if t not in already]

        if not apply:
            return result

        result["applied"] = True
        for t in result["missing"]:
            body = await _graphql(
                db,
                _WEBHOOK_SUBSCRIPTION_CREATE,
                {
                    "topic": t,
                    "webhookSubscription": {
                        "callbackUrl": callback_url,
                        "format": "JSON",
                    },
                },
            )
            err = _user_errors(body, "webhookSubscriptionCreate")
            if err:
                result["ok"] = False
                result["errors"].append(f"{t}: {err}")
                continue
            sub = (
                (body.get("data") or {}).get("webhookSubscriptionCreate") or {}
            ).get("webhookSubscription") or {}
            result["created"].append({"topic": t, "id": sub.get("id")})

        # Cutover cleanup: optionally DELETE the surfaced conflicts (same topic
        # still pointing at a different URL, e.g. BVI) so each topic delivers
        # exactly once after the baton hand-off. Off by default (conflicts are
        # only surfaced). Each delete is fail-soft + recorded.
        #
        # SAFETY (finding #16): a conflict's old (BVI) subscription is deleted
        # ONLY once a WORKING subscription at the IMS callback URL provably exists
        # for that topic -- either just created this run (in result['created']) or
        # already registered (result['already_registered']). Deleting on a FAILED
        # create would leave that topic delivering NOWHERE (a zero-receiver gap:
        # refunds/orders webhooks silently stop reaching BOTH BVI and IMS). Order
        # is therefore create/verify IMS sub -> THEN delete the conflict.
        if delete_conflicts:
            safe_topics = {
                c.get("topic") for c in result["created"]
            } | set(result["already_registered"])
            for c in result["conflicts"]:
                topic = c.get("topic")
                if topic not in safe_topics:
                    # No confirmed IMS replacement for this topic -> keep the old
                    # subscription (do NOT create a zero-receiver gap).
                    result["ok"] = False
                    result["errors"].append(
                        f"skipped delete for {topic}: replacement create at IMS URL "
                        "did not succeed -- old subscription kept to avoid a "
                        "zero-receiver gap"
                    )
                    continue
                del_res = await delete_webhook_subscription(db, c.get("id"))
                if del_res.get("ok") and del_res.get("deleted"):
                    result["deleted_conflicts"].append(
                        {"topic": topic, "id": c.get("id")}
                    )
                else:
                    result["ok"] = False
                    result["errors"].append(
                        f"delete conflict {topic}: {del_res.get('errors')}"
                    )
        return result
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        result["ok"] = False
        result["errors"].append(str(e))
        return result
