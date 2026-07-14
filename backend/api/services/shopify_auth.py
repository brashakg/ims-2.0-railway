"""
IMS 2.0 - Shopify Admin API credential resolver
================================================
ONE place that answers "what shop + access token do I use to call the Shopify
Admin API right now?" -- and answers it CORRECTLY.

WHY THIS EXISTS (the bug it fixes):
The Admin API token stored ENCRYPTED in Mongo `integrations` (type=shopify,
config.access_token) went STALE and now returns HTTP 401 on the Admin API. A
static stored token is the WRONG model for how our Shopify custom app is
configured: the working path is OAuth CLIENT-CREDENTIALS -- the exact scheme the
BVI e-commerce app uses (ecommerce/src/lib/shopify.ts) -- where we MINT a
short-lived token on demand from SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET. A
minted client-credentials token expires (typically within the day), so it must
be minted-and-cached, never persisted as a fixed value.

RESOLUTION ORDER (first usable wins):
  1. "oauth"  -- OAuth client-credentials. Preferred whenever SHOPIFY_CLIENT_ID
                 and SHOPIFY_CLIENT_SECRET are set AND a shop is known (from the
                 SHOPIFY_STORE_URL env var OR the Mongo integrations
                 config.shop_url). We POST to
                 https://{shop}/admin/oauth/access_token and cache the returned
                 token in-process with a TTL (expires_in minus a safety margin,
                 default 3600s) keyed by shop, so we mint at most ~once per TTL.
  2. "vault"  -- the decrypted static custom-app token in the Mongo
                 `integrations` collection (the previous behaviour). Kept as a
                 fallback for deployments that still use a static token.
  3. "env"    -- a static token supplied directly via env
                 (SHOPIFY_ACCESS_TOKEN / SHOPIFY_ADMIN_TOKEN), mirroring BVI's
                 SHOPIFY_LEGACY_TOKEN. Last resort.
  -> None if nothing usable.

CONTRACT: never raises (fail-soft -> None), and NEVER logs the token value --
only the resolved source + shop. The single network boundary is
`_mint_oauth_token`; tests monkeypatch it (or httpx) so no real Shopify call is
ever made in a test path.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# OAuth client-credentials token cache: shop -> (token, expires_at_epoch_seconds).
# Process-local by design: a client-credentials token is short-lived, so caching
# it in-process (not persisting it) is the correct model -- a restart just mints
# a fresh one on first use.
_token_cache: Dict[str, Tuple[str, float]] = {}

# Token lifetime handling. Shopify returns `expires_in` (seconds); we shave a
# safety margin so a token is never used right up to its expiry, and fall back
# to a conservative default when the field is missing.
_DEFAULT_TTL_SECONDS = 3600
_TTL_MARGIN_SECONDS = 300
_MIN_TTL_SECONDS = 60

_OAUTH_TIMEOUT = float(os.getenv("NEXUS_PROVIDER_TIMEOUT", "30.0"))


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _normalize_shop(raw: Any) -> str:
    """Return the BARE shop host (e.g. 'better-vision.myshopify.com') suitable
    for building https://{shop}/admin/... URLs -- strip any scheme + trailing
    slash. Every Admin API caller in the repo does f"https://{shop}/admin/...",
    so a stored value that already carries a scheme must be normalised."""
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    for pref in ("https://", "http://"):
        if low.startswith(pref):
            s = s[len(pref):]
            break
    return s.rstrip("/")


def _vault_config(db) -> Dict[str, Any]:
    """The decrypted Mongo integrations config for shopify. Fail-soft -> {}.
    Imported lazily to avoid an import cycle (nexus_providers imports this
    module for its own credential resolution)."""
    try:
        from agents.nexus_providers import _load_integration_config

        return _load_integration_config(db, "shopify") or {}
    except Exception:  # noqa: BLE001 -- a config read must never raise here
        return {}


def _resolve_shop(db, vault: Optional[Dict[str, Any]] = None) -> str:
    """The shop host to mint/auth against: SHOPIFY_STORE_URL env wins, else the
    Mongo integrations config.shop_url. Both normalised to a bare host."""
    shop = _normalize_shop(_env("SHOPIFY_STORE_URL"))
    if shop:
        return shop
    cfg = vault if vault is not None else _vault_config(db)
    return _normalize_shop(cfg.get("shop_url"))


def _cached_token(shop: str) -> Optional[str]:
    """A still-valid cached OAuth token for shop, or None (expired -> evicted)."""
    entry = _token_cache.get(shop)
    if not entry:
        return None
    token, expires_at = entry
    if time.time() < expires_at:
        return token
    _token_cache.pop(shop, None)
    return None


def _store_token(shop: str, token: str, expires_in: Any) -> None:
    """Cache a minted token with a TTL derived from expires_in (minus margin)."""
    try:
        ttl = int(expires_in)
    except (TypeError, ValueError):
        ttl = _DEFAULT_TTL_SECONDS
    if ttl <= 0:
        ttl = _DEFAULT_TTL_SECONDS
    ttl = max(ttl - _TTL_MARGIN_SECONDS, _MIN_TTL_SECONDS)
    _token_cache[shop] = (token, time.time() + ttl)


def clear_cached_tokens() -> None:
    """Force a fresh mint on the next resolve for every shop (e.g. after a
    Shopify app scope change). Mirrors BVI's clearCachedShopifyToken()."""
    _token_cache.clear()


def _mint_oauth_token(
    shop: str, client_id: str, client_secret: str
) -> Optional[Dict[str, Any]]:
    """Mint a Shopify Admin API access token via the OAuth CLIENT-CREDENTIALS
    grant (POST https://{shop}/admin/oauth/access_token). Returns the parsed
    JSON body ({access_token, expires_in, ...}) or None on any failure.

    The ONLY network boundary in this module. Never raises; never logs the
    token (logs only the shop + failure status)."""
    url = f"https://{shop}/admin/oauth/access_token"
    try:
        with httpx.Client(timeout=_OAUTH_TIMEOUT) as client:
            resp = client.post(
                url,
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                },
            )
    except Exception as e:  # noqa: BLE001 -- fail-soft: caller falls back
        logger.warning(
            "[SHOPIFY_AUTH] OAuth token mint request failed for shop=%s: %s", shop, e
        )
        return None
    if resp.status_code not in (200, 201):
        logger.warning(
            "[SHOPIFY_AUTH] OAuth token mint returned HTTP %s for shop=%s",
            resp.status_code,
            shop,
        )
        return None
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return body or None


def resolve_shopify_credentials(db) -> Optional[Dict[str, str]]:
    """Resolve usable Shopify Admin API credentials.

    Returns {"shop_url": <bare host>, "access_token": <token>, "source":
    "oauth"|"vault"|"env"} or None when nothing usable is configured.

    Preference: OAuth client-credentials (minted + cached) > Mongo vault token >
    env static token. Never raises; never logs the token value. See the module
    docstring for the full rationale (the stored vault token is stale/401s;
    OAuth mint-and-cache is the correct model)."""
    client_id = _env("SHOPIFY_CLIENT_ID")
    client_secret = _env("SHOPIFY_CLIENT_SECRET")
    vault = _vault_config(db)

    # 1) OAuth client-credentials -- the working path. Mint-and-cache.
    if client_id and client_secret:
        shop = _resolve_shop(db, vault)
        if shop:
            cached = _cached_token(shop)
            if cached:
                return {"shop_url": shop, "access_token": cached, "source": "oauth"}
            body = _mint_oauth_token(shop, client_id, client_secret)
            token = (body or {}).get("access_token")
            if token:
                _store_token(shop, token, (body or {}).get("expires_in"))
                logger.info(
                    "[SHOPIFY_AUTH] resolved credentials via source=oauth shop=%s",
                    shop,
                )
                return {"shop_url": shop, "access_token": token, "source": "oauth"}
            logger.warning(
                "[SHOPIFY_AUTH] OAuth env creds present but mint failed shop=%s; "
                "falling back to vault/env",
                shop,
            )

    # 2) Vault -- the decrypted static custom-app token in Mongo integrations.
    v_shop = _normalize_shop(vault.get("shop_url"))
    v_token = vault.get("access_token")
    if v_shop and v_token:
        logger.info(
            "[SHOPIFY_AUTH] resolved credentials via source=vault shop=%s", v_shop
        )
        return {"shop_url": v_shop, "access_token": v_token, "source": "vault"}

    # 3) Env static token (BVI-style SHOPIFY_ACCESS_TOKEN / SHOPIFY_ADMIN_TOKEN).
    e_token = _env("SHOPIFY_ACCESS_TOKEN") or _env("SHOPIFY_ADMIN_TOKEN")
    if e_token:
        e_shop = _resolve_shop(db, vault)
        if e_shop:
            logger.info(
                "[SHOPIFY_AUTH] resolved credentials via source=env shop=%s", e_shop
            )
            return {"shop_url": e_shop, "access_token": e_token, "source": "env"}

    return None
