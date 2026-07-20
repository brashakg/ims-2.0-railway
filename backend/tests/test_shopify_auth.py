"""
Tests for the Shopify Admin API credential resolver (api.services.shopify_auth).

***** SAFETY-CRITICAL: every Shopify network call is MOCKED. *****
The single network boundary `shopify_auth._mint_oauth_token` (and, in one test,
`shopify_auth.httpx.Client`) is monkeypatched, so NO real Shopify OAuth request
is ever made.

Covers:
  - OAuth client-credentials is PREFERRED when SHOPIFY_CLIENT_ID/SECRET + a shop
    are present; the minted token is cached (second call within TTL does NOT
    re-mint).
  - Shop resolves from SHOPIFY_STORE_URL env, else the Mongo vault config.shop_url.
  - Vault fallback (decrypted Mongo token) when OAuth env creds are absent.
  - Env static-token fallback (SHOPIFY_ACCESS_TOKEN / SHOPIFY_ADMIN_TOKEN).
  - None when nothing usable; OAuth mint failure falls back to the vault token.
  - The token VALUE is never returned by accident and never logged.
  - Integration: shopify_push._has_shopify_creds now succeeds via OAuth even when
    the stored Mongo token is a known-bad placeholder (the bug this fixes).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_shopify_auth.py -q
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import shopify_auth  # noqa: E402


_SHOPIFY_ENV = (
    "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET",
    "SHOPIFY_STORE_URL",
    "SHOPIFY_ACCESS_TOKEN",
    "SHOPIFY_ADMIN_TOKEN",
)


@pytest.fixture(autouse=True)
def _clean_env_and_cache(monkeypatch):
    """Isolate every test: clear the process-local token cache and unset all
    Shopify env vars so one test's env can never leak into another."""
    for name in _SHOPIFY_ENV:
        monkeypatch.delenv(name, raising=False)
    shopify_auth.clear_cached_tokens()
    yield
    shopify_auth.clear_cached_tokens()


class _MintSpy:
    """A fake _mint_oauth_token: counts calls, returns a canned OAuth body."""

    def __init__(self, token="shpat_minted_TOKEN", expires_in=3600):
        self.calls = []
        self._token = token
        self._expires_in = expires_in

    def __call__(self, shop, client_id, client_secret):
        self.calls.append({"shop": shop, "client_id": client_id})
        return {"access_token": self._token, "expires_in": self._expires_in}


# ---------------------------------------------------------------------------
# OAuth client-credentials: preferred + cached
# ---------------------------------------------------------------------------


def test_oauth_preferred_and_cached(monkeypatch):
    """With OAuth env creds + a shop, resolve mints via OAuth (source=oauth) and
    caches -- a second call within the TTL does NOT re-mint."""
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "bv.myshopify.com")
    # Even a present (but stale) vault token must NOT win over OAuth.
    monkeypatch.setattr(
        shopify_auth, "_vault_config",
        lambda db, storefront_id="BV": {"shop_url": "bv.myshopify.com", "access_token": "STALE_BAD"},
    )
    spy = _MintSpy(token="shpat_fresh")
    monkeypatch.setattr(shopify_auth, "_mint_oauth_token", spy)

    first = shopify_auth.resolve_shopify_credentials(object())
    assert first == {
        "shop_url": "bv.myshopify.com",
        "access_token": "shpat_fresh",
        "source": "oauth",
    }
    assert len(spy.calls) == 1  # minted once

    second = shopify_auth.resolve_shopify_credentials(object())
    assert second["access_token"] == "shpat_fresh" and second["source"] == "oauth"
    assert len(spy.calls) == 1  # served from cache -- NO re-mint


def test_oauth_shop_from_vault_when_env_store_absent(monkeypatch):
    """No SHOPIFY_STORE_URL -> the shop is taken from the Mongo config.shop_url,
    still minting via OAuth."""
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(
        shopify_auth, "_vault_config",
        lambda db, storefront_id="BV": {"shop_url": "https://from-vault.myshopify.com/"},
    )
    spy = _MintSpy()
    monkeypatch.setattr(shopify_auth, "_mint_oauth_token", spy)

    res = shopify_auth.resolve_shopify_credentials(object())
    assert res["source"] == "oauth"
    # shop_url is normalised to a bare host (scheme + trailing slash stripped).
    assert res["shop_url"] == "from-vault.myshopify.com"
    assert spy.calls[0]["shop"] == "from-vault.myshopify.com"


def test_oauth_mint_failure_falls_back_to_vault(monkeypatch):
    """OAuth env creds present but the mint fails (-> None) -> fall back to the
    decrypted vault token rather than returning nothing."""
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "bv.myshopify.com")
    monkeypatch.setattr(
        shopify_auth, "_vault_config",
        lambda db, storefront_id="BV": {"shop_url": "bv.myshopify.com", "access_token": "vault_tok"},
    )
    monkeypatch.setattr(shopify_auth, "_mint_oauth_token", lambda *a: None)

    res = shopify_auth.resolve_shopify_credentials(object())
    assert res == {
        "shop_url": "bv.myshopify.com",
        "access_token": "vault_tok",
        "source": "vault",
    }


# ---------------------------------------------------------------------------
# Fallbacks: vault, env static token, and None
# ---------------------------------------------------------------------------


def test_vault_fallback_when_no_oauth_env(monkeypatch):
    """No OAuth env creds at all -> the decrypted Mongo vault token is used
    (source=vault) and the mint boundary is never touched."""
    def _boom(*a):  # pragma: no cover - must never run without OAuth creds
        raise AssertionError("must not mint without OAuth env creds")

    monkeypatch.setattr(shopify_auth, "_mint_oauth_token", _boom)
    monkeypatch.setattr(
        shopify_auth, "_vault_config",
        lambda db, storefront_id="BV": {"shop_url": "bv.myshopify.com", "access_token": "vault_only"},
    )
    res = shopify_auth.resolve_shopify_credentials(object())
    assert res == {
        "shop_url": "bv.myshopify.com",
        "access_token": "vault_only",
        "source": "vault",
    }


def test_env_static_token_fallback(monkeypatch):
    """No OAuth creds, empty vault, but a static SHOPIFY_ACCESS_TOKEN + a shop ->
    source=env."""
    monkeypatch.setenv("SHOPIFY_STORE_URL", "bv.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "env_static_tok")
    monkeypatch.setattr(shopify_auth, "_vault_config", lambda db, storefront_id="BV": {})
    res = shopify_auth.resolve_shopify_credentials(object())
    assert res == {
        "shop_url": "bv.myshopify.com",
        "access_token": "env_static_tok",
        "source": "env",
    }


def test_returns_none_when_nothing_configured(monkeypatch):
    """Nothing configured (no OAuth env, empty vault, no env token) -> None,
    never a raise."""
    monkeypatch.setattr(shopify_auth, "_vault_config", lambda db, storefront_id="BV": {})
    assert shopify_auth.resolve_shopify_credentials(object()) is None


def test_vault_config_failsoft_on_read_error(monkeypatch):
    """A blowing-up Mongo read is swallowed by _vault_config (-> {}), so resolve
    stays fail-soft and returns None when there is nothing else usable."""
    from agents import nexus_providers as nx

    def _explode(db, t, storefront_id=None):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(nx, "_load_integration_config", _explode)
    assert shopify_auth._vault_config(object()) == {}
    assert shopify_auth.resolve_shopify_credentials(object()) is None


# ---------------------------------------------------------------------------
# The token value is never logged; the real HTTP mint sends client_credentials
# ---------------------------------------------------------------------------


def test_token_never_logged(monkeypatch, caplog):
    """Only the source + shop are logged -- never the token value."""
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "bv.myshopify.com")
    monkeypatch.setattr(shopify_auth, "_vault_config", lambda db, storefront_id="BV": {})
    secret_token = "shpat_SUPER_SECRET_VALUE_9999"
    monkeypatch.setattr(
        shopify_auth, "_mint_oauth_token",
        lambda *a: {"access_token": secret_token, "expires_in": 3600},
    )
    with caplog.at_level(logging.INFO, logger="api.services.shopify_auth"):
        res = shopify_auth.resolve_shopify_credentials(object())
    assert res["access_token"] == secret_token  # returned to the caller...
    assert secret_token not in caplog.text  # ...but NEVER logged
    assert "oauth" in caplog.text and "bv.myshopify.com" in caplog.text


def test_http_mint_sends_client_credentials_grant(monkeypatch):
    """Exercise the REAL _mint_oauth_token with httpx.Client mocked: it POSTs the
    client-credentials grant to the shop's oauth endpoint and returns the token."""
    captured = {}

    class _FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "shpat_from_http", "expires_in": 7200}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResp()

    monkeypatch.setattr(shopify_auth.httpx, "Client", _FakeClient)
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "the_cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "the_secret")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "https://bv.myshopify.com")
    monkeypatch.setattr(shopify_auth, "_vault_config", lambda db, storefront_id="BV": {})

    res = shopify_auth.resolve_shopify_credentials(object())
    assert res == {
        "shop_url": "bv.myshopify.com",
        "access_token": "shpat_from_http",
        "source": "oauth",
    }
    # POSTed to the shop's oauth endpoint (scheme normalised off the env value).
    assert captured["url"] == "https://bv.myshopify.com/admin/oauth/access_token"
    assert captured["json"]["grant_type"] == "client_credentials"
    assert captured["json"]["client_id"] == "the_cid"
    assert captured["json"]["client_secret"] == "the_secret"


# ---------------------------------------------------------------------------
# Integration: the push gate resolves via OAuth even with a stale vault token
# ---------------------------------------------------------------------------


def test_push_has_creds_true_via_oauth_despite_bad_vault_token(monkeypatch):
    """The bug this fixes: with OAuth env creds set, shopify_push._has_shopify_creds
    reports TRUE and the resolver returns the freshly-minted OAuth token -- even
    though the stored Mongo token is a known-bad 401 placeholder."""
    from agents import nexus_providers as nx
    from api.services import shopify_push

    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SHOPIFY_STORE_URL", "bv.myshopify.com")
    # The stored Mongo token is the stale placeholder that 401s the Admin API.
    monkeypatch.setattr(
        nx, "_load_integration_config",
        lambda db, t, storefront_id=None: {"shop_url": "bv.myshopify.com",
                                           "access_token": "STALE_401_PLACEHOLDER"},
    )
    monkeypatch.setattr(
        shopify_auth, "_mint_oauth_token",
        lambda *a: {"access_token": "shpat_fresh_oauth", "expires_in": 3600},
    )

    assert shopify_push._has_shopify_creds(object()) is True
    resolved = shopify_push.resolve_shopify_credentials(object())
    assert resolved["source"] == "oauth"
    assert resolved["access_token"] == "shpat_fresh_oauth"  # NOT the stale token
