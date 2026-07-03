"""
IMS 2.0 - Catalog Autopilot in-app data sources
================================================
Covers the ADDITIVE wiring that lets the owner enable the two Catalog-Autopilot
data sources from Settings -> Integrations (encrypted at rest) instead of
Railway env vars, while env vars still work as a fallback:

  1. MyLuxotticaAdapter.is_enabled() is False with no config, and True when
     integration_config.get_myluxottica_config() returns creds (patched on the
     catalog_autopilot module reference).
  2. MarketplaceAdapter.is_enabled() is True when get_websearch_config() returns
     provider "google_cse".
  3. The integrations catalog includes types "myluxottica" and "web_search" with
     the password / api_key fields marked secret (so they encrypt + mask).

NO real network call happens: the kill-switch AUTOPILOT_DISABLE_NETWORK is
cleared only where an is_enabled() path requires it, and no adapter search runs.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

from api.services import catalog_autopilot as ap  # noqa: E402
from api.services import integration_config as ic  # noqa: E402


# ---------------------------------------------------------------------------
# 1. MyLuxotticaAdapter -- DB/env creds gate is_enabled()
# ---------------------------------------------------------------------------


class TestMyLuxotticaInApp:
    def test_disabled_with_no_config(self, monkeypatch):
        # No DB config, no env creds -> disabled.
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setattr(ic, "get_myluxottica_config", lambda: {})
        assert ap.MyLuxotticaAdapter().is_enabled() is False

    def test_enabled_when_creds_configured(self, monkeypatch):
        # Creds returned by the loader (as if pasted into Settings -> Integrations)
        # -> enabled (httpx present, network not disabled).
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setattr(
            ic,
            "get_myluxottica_config",
            lambda: {
                "user": "dealer@bettervision.in",
                "password": "s3cret",
                "base_url": "https://my.essilorluxottica.com",
            },
        )
        assert ap.MyLuxotticaAdapter().is_enabled() is True

    def test_network_disabled_forces_off(self, monkeypatch):
        # Even with creds, the hard kill-switch keeps it disabled.
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "1")
        monkeypatch.setattr(
            ic,
            "get_myluxottica_config",
            lambda: {"user": "u", "password": "p", "base_url": ""},
        )
        assert ap.MyLuxotticaAdapter().is_enabled() is False

    def test_base_url_prefers_db_then_default(self, monkeypatch):
        monkeypatch.setattr(
            ic,
            "get_myluxottica_config",
            lambda: {"user": "u", "password": "p", "base_url": "https://portal.test"},
        )
        assert ap.MyLuxotticaAdapter()._base_url() == "https://portal.test"
        # Blank base_url -> falls back to the module default (EssilorLuxottica).
        monkeypatch.setattr(
            ic,
            "get_myluxottica_config",
            lambda: {"user": "u", "password": "p", "base_url": ""},
        )
        assert ap.MyLuxotticaAdapter()._base_url() == ap.MYLUXOTTICA_BASE_URL

    def test_default_base_url_is_essilorluxottica(self):
        assert ap.MYLUXOTTICA_BASE_URL == "https://my.essilorluxottica.com"


# ---------------------------------------------------------------------------
# 2. MarketplaceAdapter -- web-search provider gates is_enabled()
# ---------------------------------------------------------------------------


class TestMarketplaceInApp:
    def test_enabled_when_provider_google_cse(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setattr(
            ic,
            "get_websearch_config",
            lambda: {
                "google_cse_key": "k",
                "google_cse_cx": "cx",
                "serp_api_key": "",
                "provider": "google_cse",
            },
        )
        adapter = ap.MarketplaceAdapter()
        assert adapter.is_enabled() is True
        assert adapter._has_cse() is True
        assert adapter._has_serp() is False

    def test_enabled_when_provider_serpapi(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setattr(
            ic,
            "get_websearch_config",
            lambda: {
                "google_cse_key": "",
                "google_cse_cx": "",
                "serp_api_key": "serp",
                "provider": "serpapi",
            },
        )
        assert ap.MarketplaceAdapter().is_enabled() is True

    def test_disabled_when_no_provider(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "0")
        monkeypatch.setattr(
            ic,
            "get_websearch_config",
            lambda: {
                "google_cse_key": "",
                "google_cse_cx": "",
                "serp_api_key": "",
                "provider": "",
            },
        )
        assert ap.MarketplaceAdapter().is_enabled() is False

    def test_network_disabled_forces_off(self, monkeypatch):
        monkeypatch.setenv("AUTOPILOT_DISABLE_NETWORK", "1")
        monkeypatch.setattr(
            ic,
            "get_websearch_config",
            lambda: {"provider": "google_cse", "google_cse_key": "k", "google_cse_cx": "cx"},
        )
        assert ap.MarketplaceAdapter().is_enabled() is False


# ---------------------------------------------------------------------------
# 3. integration_config loaders: DB-first, env fallback
# ---------------------------------------------------------------------------


class TestLoaders:
    def test_myluxottica_env_fallback(self, monkeypatch):
        monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
        monkeypatch.setenv("MYLUXOTTICA_USER", "env-user")
        monkeypatch.setenv("MYLUXOTTICA_PASS", "env-pass")
        monkeypatch.delenv("MYLUXOTTICA_BASE_URL", raising=False)
        cfg = ic.get_myluxottica_config()
        assert cfg == {"user": "env-user", "password": "env-pass", "base_url": ""}

    def test_myluxottica_db_wins_over_env(self, monkeypatch):
        monkeypatch.setattr(
            ic,
            "_load_db_config",
            lambda _type: {"user": "db-user", "password": "db-pass", "base_url": "https://x"},
        )
        monkeypatch.setenv("MYLUXOTTICA_USER", "env-user")
        monkeypatch.setenv("MYLUXOTTICA_PASS", "env-pass")
        cfg = ic.get_myluxottica_config()
        assert cfg["user"] == "db-user"
        assert cfg["password"] == "db-pass"
        assert cfg["base_url"] == "https://x"

    def test_myluxottica_empty_when_incomplete(self, monkeypatch):
        monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
        monkeypatch.setenv("MYLUXOTTICA_USER", "only-user")
        monkeypatch.delenv("MYLUXOTTICA_PASS", raising=False)
        assert ic.get_myluxottica_config() == {}

    def test_websearch_google_cse_from_env(self, monkeypatch):
        monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
        monkeypatch.setenv("GOOGLE_CSE_KEY", "gk")
        monkeypatch.setenv("GOOGLE_CSE_CX", "gcx")
        monkeypatch.delenv("SERP_API_KEY", raising=False)
        cfg = ic.get_websearch_config()
        assert cfg["provider"] == "google_cse"
        assert cfg["google_cse_key"] == "gk"
        assert cfg["google_cse_cx"] == "gcx"

    def test_websearch_serpapi_from_env(self, monkeypatch):
        monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
        monkeypatch.delenv("GOOGLE_CSE_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
        monkeypatch.setenv("SERP_API_KEY", "serp")
        cfg = ic.get_websearch_config()
        assert cfg["provider"] == "serpapi"
        assert cfg["serp_api_key"] == "serp"

    def test_websearch_db_key_cx_wins(self, monkeypatch):
        monkeypatch.setattr(
            ic, "_load_db_config", lambda _type: {"api_key": "db-key", "cx": "db-cx"}
        )
        monkeypatch.delenv("GOOGLE_CSE_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
        monkeypatch.delenv("SERP_API_KEY", raising=False)
        cfg = ic.get_websearch_config()
        assert cfg["provider"] == "google_cse"
        assert cfg["google_cse_key"] == "db-key"
        assert cfg["google_cse_cx"] == "db-cx"

    def test_websearch_empty_when_nothing_set(self, monkeypatch):
        monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
        monkeypatch.delenv("GOOGLE_CSE_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
        monkeypatch.delenv("SERP_API_KEY", raising=False)
        assert ic.get_websearch_config()["provider"] == ""


# ---------------------------------------------------------------------------
# 4. Catalog surfaces the two new sources with secret fields
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_catalog_includes_new_sources(self):
        from api.routers.settings import _INTEGRATION_CATALOG

        by_type = {e["type"]: e for e in _INTEGRATION_CATALOG}
        assert "myluxottica" in by_type
        assert "web_search" in by_type
        # Both live under the Commerce group.
        assert by_type["myluxottica"]["category"] == "Commerce"
        assert by_type["web_search"]["category"] == "Commerce"

    def test_myluxottica_password_field_is_secret(self):
        from api.routers.settings import _INTEGRATION_CATALOG

        entry = next(e for e in _INTEGRATION_CATALOG if e["type"] == "myluxottica")
        fields = {f["key"]: f for f in entry["fields"]}
        assert fields["password"]["secret"] is True
        assert fields["user"]["secret"] is False
        assert fields["base_url"]["secret"] is False

    def test_web_search_api_key_field_is_secret(self):
        from api.routers.settings import _INTEGRATION_CATALOG

        entry = next(e for e in _INTEGRATION_CATALOG if e["type"] == "web_search")
        fields = {f["key"]: f for f in entry["fields"]}
        assert fields["api_key"]["secret"] is True
        assert fields["cx"]["secret"] is False

    def test_new_secret_fields_are_encrypted_and_masked(self):
        """password + api_key must be recognized by the at-rest encryption /
        mask layer (which keys off the FIELD NAME, not the catalog secret flag)."""
        from api.routers.settings import (
            _SENSITIVE_FIELDS,
            _encrypt_config,
            _decrypt_config,
            _mask_config,
        )

        assert "password" in _SENSITIVE_FIELDS
        assert "api_key" in _SENSITIVE_FIELDS

        cfg = {"user": "u", "password": "portal-pass", "api_key": "goog-key", "cx": "cx1"}
        enc = _encrypt_config(cfg)
        assert enc["password"] != "portal-pass"
        assert enc["api_key"] != "goog-key"
        # Non-secret fields pass through untouched.
        assert enc["user"] == "u"
        assert enc["cx"] == "cx1"
        # Round-trips.
        dec = _decrypt_config(enc)
        assert dec["password"] == "portal-pass"
        assert dec["api_key"] == "goog-key"
        # Masking hides the secret values.
        masked = _mask_config(cfg)
        assert "*" in masked["password"]
        assert "*" in masked["api_key"]
