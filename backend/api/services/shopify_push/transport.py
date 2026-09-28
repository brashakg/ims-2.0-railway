"""Shopify push -- transport

The single Shopify network boundary: `_graphql` (bounded
retry/backoff, Retry-After honoured) over `_post_once`, the userErrors
extractor, the PUBLISH_SCOPE_MISSING code and `_now`. Tests monkeypatch
`_graphql` by the package path so no real Shopify call ever happens.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import asyncio
import httpx
import random
import re

from agents.nexus_providers import SHOPIFY_API_VERSION
from api.services.shopify_auth import resolve_shopify_credentials

from ._shared import PROVIDER_TIMEOUT, logger

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

# SEND-ONCE (2026-09-28). A read timeout or a 5xx comes back AFTER the request
# left, so Shopify may already have applied it. Replaying a query, or a
# mutation whose second run changes nothing (an update, a set, a delete, a
# publish), is safe; replaying a CREATE mints a second object -- one
# productCreateMedia timeout after Shopify committed plus a successful retry
# was two photographs on the listing, one of them unmanaged forever. So only
# the mutations named here are replayed after a send; every other mutation --
# the creates, and any mutation added later without a name here (default
# deny) -- is sent ONCE and a lost answer raises. A connect/pool timeout (the
# request never left), a 429 and a THROTTLED body (Shopify refused it
# unapplied) are retried for every operation, as before.
# Send-once today: imsProductCreate, imsProductCreateMedia,
# imsVariantsBulkCreate, imsCollectionCreate, imsMenuCreate,
# imsFulfillmentCreate, imsWebhookSubscriptionCreate. A lost answer raises
# SentOnce; whoever sent it owns the NEXT pass, which reads Shopify first
# (the photo pass settles by name on the product's next press; the
# fulfilment re-reads its FulfillmentOrders before it creates again).
_OP = re.compile(r"\s*mutation\s+(\w+)")
_REPLAY_SAFE = frozenset(
    {
        "imsProductUpdate",
        "imsTagsAdd",
        "imsTagsRemove",
        "imsCollectionUpdate",
        "imsCollectionAddProducts",
        "imsMenuUpdate",
        "metafieldsSet",
        "imsPublishablePublish",
        "imsVariantPricesUpdate",
        "imsVariantInventoryUpdate",
        "imsInventorySetQuantities",
        "imsInventoryActivate",
        "imsProductDeleteMedia",
        "imsProductReorderMedia",
        "imsWebhookSubscriptionDelete",
    }
)


class SentOnce(ValueError):
    """A send-once mutation left and its answer was lost (a read timeout or a
    5xx): Shopify may have applied it. The caller's next pass must READ what
    Shopify holds before it sends again -- never re-send blind."""


def _replay_safe(query: str) -> bool:
    """May this operation be sent again after Shopify may have applied it? A
    query yes; a mutation only when _REPLAY_SAFE names it (default deny)."""
    m = _OP.match(query or "")
    return (m.group(1) in _REPLAY_SAFE) if m else not (query or "").lstrip().startswith("mutation")


def _op_name(query: str) -> str:
    m = _OP.match(query or "")
    return m.group(1) if m else "operation"


_version_logged = False


def _log_served_version(resp: Any) -> None:
    """Log, once per process, the Admin API version Shopify actually served
    (it can differ from the pinned one when that version is unsupported)."""
    global _version_logged
    if _version_logged:
        return
    _version_logged = True
    try:
        logger.info(
            "[SHOPIFY_PUSH] Shopify served API version %s (pinned %s)",
            resp.headers.get("X-Shopify-API-Version"),
            SHOPIFY_API_VERSION,
        )
    except Exception:  # noqa: BLE001 -- a log line must never fail a push
        pass


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
    present). Non-retryable 4xx raises immediately. SEND-ONCE: a read
    timeout or a 5xx on a mutation _REPLAY_SAFE does not name raises at once
    (Shopify may have applied it; see _REPLAY_SAFE).

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
    safe = _replay_safe(query)
    sent_once = (
        "shopify %s %%s; not retried: Shopify may have applied it" % _op_name(query)
    )

    last_error = "unknown"
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = await _post_once(url, headers, payload)
        except httpx.TimeoutException as e:
            if not safe and not isinstance(e, (httpx.ConnectTimeout, httpx.PoolTimeout)):
                raise SentOnce(sent_once % ("timed out after sending (%s)" % e))
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
        _log_served_version(resp)
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

        if status >= 500 and not safe:
            raise SentOnce(sent_once % ("answered status %d (%s)" % (status, resp.text[:200])))
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


def _user_error_codes(body: Dict[str, Any], mutation_field: str) -> set:
    """The machine `code` of every userError on the named mutation field, as a
    set -- `_user_errors` flattens to a string and cannot be keyed on. Empty
    on a clean body, a malformed body, or errors that carry no code."""
    try:
        data = (body or {}).get("data") or {}
        ue = (data.get(mutation_field) or {}).get("userErrors") or []
        return {str(e["code"]) for e in ue if isinstance(e, dict) and e.get("code")}
    except Exception:  # noqa: BLE001
        return set()


PUBLISH_SCOPE_MISSING = "PUBLISH_SCOPE_MISSING"
_PUBLISH_SCOPE_MISSING_MSG = (
    "Saved on Shopify but not made visible: the IMS app has no "
    "write_publications access. Re-approve the app's permissions in Shopify, "
    "then press again."
)


def _is_access_denied(body: Any) -> bool:
    """True when a transport-200 GraphQL body carries Shopify's ACCESS_DENIED
    (a missing access scope on the app token). Fail-soft -> False."""
    try:
        for e in (body or {}).get("errors") or []:
            if isinstance(e, dict) and (
                (e.get("extensions") or {}).get("code") == "ACCESS_DENIED"
            ):
                return True
            if "access denied" in str(e).lower():
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _now() -> datetime:
    return datetime.now(timezone.utc)

