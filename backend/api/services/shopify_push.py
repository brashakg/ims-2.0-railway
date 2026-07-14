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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ===========================================================================
# Gating -- the single source of truth for "are we DARK or LIVE?"
# ===========================================================================


def push_mode_status(db) -> Dict[str, Any]:
    """Report the CURRENT push posture without pushing anything (drives GET
    /online-store/push/status). Pure read; never raises.

    Returns the three gate components + the derived effective mode so the owner
    (and the UI banner) can see exactly why pushes are DARK or LIVE:
      writes_enabled  -- IMS_SHOPIFY_WRITES on?
      dispatch_mode   -- off / test / live
      creds_present   -- shop_url + access_token in the integrations collection?
      mode            -- LIVE only when all three align, else SIMULATED.
    """
    writes = ims_shopify_writes_enabled()
    disp = shopify_dispatch_mode()
    creds = _has_shopify_creds(db)
    live = bool(writes and disp == "live" and creds)
    return {
        "mode": MODE_LIVE if live else MODE_SIMULATED,
        "writes_enabled": writes,
        "dispatch_mode": disp,
        "creds_present": creds,
        "is_live": live,
        "api_version": SHOPIFY_API_VERSION,
        "single_writer_note": (
            "BVI is the single Shopify writer until the Phase-6 baton cutover; "
            "IMS push is DARK until IMS_SHOPIFY_WRITES=1 AND "
            "SHOPIFY_DISPATCH_MODE=live (or global DISPATCH_MODE=live)."
        ),
    }


def _has_shopify_creds(db) -> bool:
    """True iff usable Shopify Admin API credentials resolve (shop_url +
    access_token), via OAuth client-credentials OR the vault/env fallback --
    NOT a raw read of the (possibly stale) stored token. So the gate now reports
    creds-present whenever OAuth env creds are configured, even if the Mongo
    vault token is a known-bad placeholder. Fail-soft -> False (treated as DARK).

    NOTE: this is only ever reached (in _live_or_reason) AFTER the writes +
    dispatch gates pass, so in the DARK default posture no OAuth token is minted.
    push_mode_status calls it directly; with the in-process token cache that
    mints at most ~once per TTL."""
    try:
        creds = resolve_shopify_credentials(db)
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
    creds = resolve_shopify_credentials(db)
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

_PRODUCT_CREATE = """
mutation imsProductCreate($input: ProductInput!) {
  productCreate(input: $input) {
    product { id handle }
    userErrors { field message }
  }
}
"""

_PRODUCT_UPDATE = """
mutation imsProductUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id handle }
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
    status_map = {"DRAFT": "DRAFT", "PUBLISHED": "ACTIVE", "ARCHIVED": "ARCHIVED"}
    inp: Dict[str, Any] = {
        "title": title,
        "status": status_map.get(str(ecom.get("status") or "").upper(), "DRAFT"),
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
    if seo.get("tags"):
        inp["tags"] = list(seo["tags"])
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
    parent product's offer_price / pricing.offer_price, else the product mrp.
    MRP (the compare-at side): the variant's compare_at_price, else its mrp,
    else the product mrp. Returns 0.0 legs when nothing usable exists."""
    pricing = product.get("pricing") or {}
    price = (
        _price_float(variant.get("discounted_price"))
        or _price_float(variant.get("mrp"))
        or _price_float(product.get("offer_price"))
        or _price_float(pricing.get("offer_price"))
        or _price_float(product.get("mrp"))
        or _price_float(pricing.get("mrp"))
    )
    mrp = (
        _price_float(variant.get("compare_at_price"))
        or _price_float(variant.get("mrp"))
        or _price_float(product.get("mrp"))
        or _price_float(pricing.get("mrp"))
    )
    return price, mrp


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
        barcode = v.get("gtin") or v.get("barcode")
        if barcode:
            row["barcode"] = str(barcode)
        rows.append(row)
    return rows, skipped


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


def _writeback_product(db, product_id: str, shopify_id: str) -> None:
    """Persist ecom.shopify_product_id (+ stamps) on the catalog_products doc and
    clear the dirty flag, for idempotent re-push.

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
        ecom["last_pushed_at"] = _now()
        ecom["locally_modified"] = False
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] product write-back failed {product_id}: {e}")


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
    db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None
) -> PushResult:
    """Push a catalog product (+ its ecom sub-doc + variants) to Shopify.

    DARK by default -> returns a SIMULATED dry-run plan with the full ProductInput
    and NO network call. LIVE only when all three gates pass: then productCreate
    (no stored gid) or productUpdate (gid present), with the new gid written back
    for idempotency. Never raises."""
    pid = product.get("id") or product.get("product_id")
    # Hub Phase 5: push-lock is the FIRST gate -- a locked brand is NEVER pushed,
    # before the dark/live gate (fail-closed).
    _lock = push_lock_reason(db, "product", product)
    if _lock:
        return _blocked_result("product", pid, _lock)
    variants = variants or []
    ecom = product.get("ecom") or {}
    existing_gid = ecom.get("shopify_product_id")
    payload = build_product_input(product, variants)
    # Attribute -> metafield side channel (owner 2026-07-05): planned in the
    # dry-run, upserted via metafieldsSet after a LIVE product write succeeds.
    metafields = build_product_metafields(product)
    action = "update" if existing_gid else "create"

    live, reason = _live_or_reason(db)
    if not live:
        # Variant price/barcode plan rides on the dry-run too (owner priority:
        # a price change must be visibly part of the push plan).
        vp_plan = None
        if variants:
            vp_rows, vp_skipped = build_variant_price_inputs(product, variants)
            vp_plan = {"variants": vp_rows, "skipped_no_gid_or_price": vp_skipped}
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
        # Variant price/barcode push rides after the product write too (same
        # fail-soft side-channel contract: an error is reported on the result,
        # never flips the product push's ok). push_variant_prices never raises.
        vp_summary = None
        if variants and new_gid:
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
            ok=True,
            shopify_id=new_gid,
            payload=payload,
            metafields=mf_summary,
            variant_prices=vp_summary,
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
    rows, skipped = build_variant_price_inputs(product, variants or [])
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
    + smart ruleSet when SMART). DARK by default; LIVE behind the gates with gid
    write-back. Never raises."""
    cid = collection.get("collection_id")
    # Hub Phase 5: push-lock first -- a locked collection handle is NEVER pushed.
    _lock = push_lock_reason(db, "collection", collection)
    if _lock:
        return _blocked_result("collection", cid, _lock)
    existing_gid = collection.get("shopify_collection_id")
    payload = build_collection_input(collection)
    action = "update" if existing_gid else "create"

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
        return PushResult(
            mode=MODE_LIVE,
            entity="collection",
            action=action,
            target_id=cid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
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
        if new_gid and iid:
            _writeback_image(db, iid, new_gid)
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


def _writeback_image(db, image_id: str, shopify_id: str) -> None:
    """Persist shopify_image_id on the product_images doc. Fail-soft. (The image
    has no locally_modified flag; the gid presence is the idempotency key.)"""
    try:
        db["product_images"].update_one(
            {"image_id": image_id},
            {"$set": {"shopify_image_id": shopify_id, "updated_at": _now()}},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] image write-back failed {image_id}: {e}")


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
query imsWebhookSubscriptions($first: Int!) {
  webhookSubscriptions(first: $first) {
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
  }
}
"""

_WEBHOOK_SUBSCRIPTION_CREATE = """
mutation imsWebhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
    webhookSubscription { id topic }
    userErrors { field message }
  }
}
"""

# The receiver route the subscriptions point at (mounted under /api/v1).
_WEBHOOK_RECEIVER_PATH = "/api/v1/webhooks/shopify"


def _topic_enum(topic: str) -> str:
    """'orders/create' -> 'ORDERS_CREATE' (Shopify WebhookSubscriptionTopic).
    Already-enum input ('ORDERS_CREATE') passes through unchanged."""
    return str(topic or "").strip().replace("/", "_").replace(".", "_").upper()


async def register_webhooks(
    db,
    callback_base_url: str,
    topics: Optional[List[str]] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    """Ensure Shopify webhookSubscriptions exist for `topics`, pointing at
    {callback_base_url}/api/v1/webhooks/shopify. Fail-soft: returns a structured
    dict, never raises.

    DRY-RUN by default (apply=False): reports what WOULD be registered. When
    the three push gates are LIVE the dry-run also QUERIES the existing
    subscriptions (a read); when DARK it makes NO network call at all.
    Mutations happen ONLY when apply=True AND the gates are LIVE, and only for
    topics not already subscribed at this exact callback URL (idempotent)."""
    topic_enums = [_topic_enum(t) for t in (topics or ["orders/create"]) if _topic_enum(t)]
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
        body = await _graphql(db, _WEBHOOK_SUBSCRIPTIONS_QUERY, {"first": 100})
        if not isinstance(body, dict) or body.get("errors"):
            result["ok"] = False
            result["errors"].append(
                f"webhookSubscriptions query failed: {str((body or {}).get('errors'))[:300]}"
            )
            return result
        edges = (
            ((body.get("data") or {}).get("webhookSubscriptions") or {}).get("edges")
        ) or []
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
        return result
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        result["ok"] = False
        result["errors"].append(str(e))
        return result
