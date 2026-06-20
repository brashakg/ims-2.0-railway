"""
Tests for the unified integrations hub.

Covers:
  1. Catalog endpoint returns all expected types with required fields.
  2. Save + mask round-trip for several integration types.
  3. Env-bridge fallback for photoroom / storage / anthropic (returns env when
     no DB doc; returns DB value when DB has an enabled doc).
  4. SUPERADMIN gate: non-superadmin gets 403 on the catalog endpoint.

Run:
    JWT_SECRET_KEY=test python -m pytest backend/tests/test_integrations_settings.py -q
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers.settings import (
    _INTEGRATION_CATALOG,
    _encrypt_config,
    _decrypt_config,
    _mask_config,
)


# ---------------------------------------------------------------------------
# 1. Catalog shape
# ---------------------------------------------------------------------------

EXPECTED_TYPES = {
    "shopify", "tally", "shiprocket", "ondc",
    "whatsapp", "slack",
    "anthropic", "pagespeed", "photoroom",
    "razorpay",
    "einvoice",
    "storage",
    "google_ads", "meta_ads", "meta_whatsapp",
}


def test_catalog_contains_all_expected_types():
    catalog_types = {entry["type"] for entry in _INTEGRATION_CATALOG}
    missing = EXPECTED_TYPES - catalog_types
    assert not missing, f"Missing integration types in catalog: {missing}"


def test_catalog_entries_have_required_shape():
    for entry in _INTEGRATION_CATALOG:
        assert "type" in entry, f"Missing 'type': {entry}"
        assert "name" in entry, f"Missing 'name': {entry}"
        assert "description" in entry, f"Missing 'description': {entry}"
        assert "category" in entry, f"Missing 'category': {entry}"
        assert "fields" in entry, f"Missing 'fields': {entry}"
        assert isinstance(entry["fields"], list), f"'fields' must be a list: {entry}"
        for field in entry["fields"]:
            assert "key" in field, f"Field missing 'key' in {entry['type']}: {field}"
            assert "label" in field, f"Field missing 'label' in {entry['type']}: {field}"
            assert "secret" in field, f"Field missing 'secret' in {entry['type']}: {field}"


def test_catalog_categories_are_expected():
    allowed = {"Commerce", "Messaging", "AI", "Payments", "Compliance", "Storage", "Ads"}
    for entry in _INTEGRATION_CATALOG:
        assert entry["category"] in allowed, (
            f"Unexpected category '{entry['category']}' for type '{entry['type']}'. "
            f"Allowed: {allowed}"
        )


def test_secret_fields_are_named_consistently():
    """Secret fields must have names that appear in _SENSITIVE_FIELDS so they get
    encrypted at rest and masked on read."""
    from api.routers.settings import _SENSITIVE_FIELDS
    for entry in _INTEGRATION_CATALOG:
        for field in entry["fields"]:
            if field.get("secret"):
                assert field["key"].lower() in _SENSITIVE_FIELDS, (
                    f"Secret field '{field['key']}' in '{entry['type']}' is NOT in "
                    f"_SENSITIVE_FIELDS -- it won't be encrypted at rest or masked on read. "
                    f"Add it to _SENSITIVE_FIELDS or rename the key."
                )


# ---------------------------------------------------------------------------
# 2. Encrypt / mask round-trip
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    """Sensitive fields written to Mongo are round-tripped correctly."""
    plaintext_cfg = {
        "api_key": "sk-ant-test123",
        "model": "claude-haiku-4-5",
        "shop_url": "mystore.myshopify.com",
        "access_token": "shpat_supersecret",
        "key_id": "rzp_live_abc",
        "key_secret": "rp_secret_xyz",
    }
    encrypted = _encrypt_config(plaintext_cfg)
    # Sensitive fields must not be stored in plaintext
    for field in ("api_key", "access_token", "key_secret"):
        assert encrypted[field] != plaintext_cfg[field], (
            f"'{field}' was not encrypted"
        )
    # Non-sensitive fields pass through
    assert encrypted["model"] == "claude-haiku-4-5"
    assert encrypted["shop_url"] == "mystore.myshopify.com"
    assert encrypted["key_id"] == "rzp_live_abc"

    decrypted = _decrypt_config(encrypted)
    assert decrypted["api_key"] == plaintext_cfg["api_key"]
    assert decrypted["access_token"] == plaintext_cfg["access_token"]
    assert decrypted["key_secret"] == plaintext_cfg["key_secret"]


def test_mask_hides_sensitive_values():
    cfg = {
        "api_key": "sk-ant-test123",
        "key_id": "rzp_live_abc",
        "key_secret": "rp_secret_xyz",
        "shop_url": "mystore.myshopify.com",
    }
    masked = _mask_config(cfg)
    # Sensitive fields must contain stars
    assert "*" in masked["api_key"], "api_key not masked"
    assert "*" in masked["key_secret"], "key_secret not masked"
    # Non-sensitive fields must be intact
    assert masked["key_id"] == "rzp_live_abc"
    assert masked["shop_url"] == "mystore.myshopify.com"


def test_mask_never_returns_plaintext_secret():
    """A masked value must not equal the original plaintext."""
    plaintext = "super_secret_value_12345"
    cfg = {"api_key": plaintext}
    masked = _mask_config(_decrypt_config(_encrypt_config(cfg)))
    assert masked["api_key"] != plaintext


# ---------------------------------------------------------------------------
# 3. Env-bridge fallback helpers
# ---------------------------------------------------------------------------

def test_get_photoroom_config_returns_env_when_no_db(monkeypatch):
    """When DB is unavailable, photoroom config falls back to env vars."""
    monkeypatch.setenv("PHOTOROOM_API_KEY", "pr_env_key_123")
    monkeypatch.setenv("IMAGE_EDIT_PROVIDER", "photoroom")

    # Patch DB read to simulate no connected DB
    import api.services.integration_config as ic
    monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})

    cfg = ic.get_photoroom_config()
    assert cfg["api_key"] == "pr_env_key_123"
    assert cfg["provider"] == "photoroom"


def test_get_photoroom_config_db_wins_over_env(monkeypatch):
    """When a DB doc is present and enabled, its api_key takes priority."""
    monkeypatch.setenv("PHOTOROOM_API_KEY", "pr_env_key_123")

    import api.services.integration_config as ic
    monkeypatch.setattr(
        ic,
        "_load_db_config",
        lambda _type: {"api_key": "pr_db_key_456", "provider": "photoroom"},
    )

    cfg = ic.get_photoroom_config()
    assert cfg["api_key"] == "pr_db_key_456"


def test_get_anthropic_config_returns_env_when_no_db(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-test")
    monkeypatch.setenv("AGENT_CLAUDE_MODEL", "claude-haiku-4-5")

    import api.services.integration_config as ic
    monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})

    cfg = ic.get_anthropic_config()
    assert cfg["api_key"] == "sk-ant-env-test"
    assert cfg["model"] == "claude-haiku-4-5"


def test_get_anthropic_config_db_wins_over_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-test")

    import api.services.integration_config as ic
    monkeypatch.setattr(
        ic,
        "_load_db_config",
        lambda _type: {"api_key": "sk-ant-db-test", "model": "claude-sonnet-4-5"},
    )

    cfg = ic.get_anthropic_config()
    assert cfg["api_key"] == "sk-ant-db-test"
    assert cfg["model"] == "claude-sonnet-4-5"


def test_get_storage_config_merges_env_fallback(monkeypatch):
    monkeypatch.setenv("IMAGE_S3_BUCKET", "env-bucket")
    monkeypatch.setenv("IMAGE_S3_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("IMAGE_S3_SECRET_KEY", "env-sk")

    import api.services.integration_config as ic
    monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})

    cfg = ic.get_storage_config()
    assert cfg["bucket"] == "env-bucket"
    assert cfg["access_key"] == "env-ak"
    assert cfg["secret_key"] == "env-sk"


def test_get_storage_config_db_wins_over_env(monkeypatch):
    monkeypatch.setenv("IMAGE_S3_BUCKET", "env-bucket")

    import api.services.integration_config as ic
    monkeypatch.setattr(
        ic,
        "_load_db_config",
        lambda _type: {
            "provider": "s3",
            "bucket": "db-bucket",
            "access_key": "db-ak",
            "secret_key": "db-sk",
        },
    )

    cfg = ic.get_storage_config()
    assert cfg["bucket"] == "db-bucket"
    assert cfg["access_key"] == "db-ak"


# ---------------------------------------------------------------------------
# 4. ADMIN+SUPERADMIN gate on catalog endpoint
# ---------------------------------------------------------------------------
# The catalog endpoint was opened from SUPERADMIN-only to ADMIN+SUPERADMIN so
# the unified IntegrationsHub renders for ADMIN (who already may GET/PUT the
# integration configs the catalog merely describes). A role BELOW admin must
# still be denied. The sensitive status surfaces (IntegrationStatusCard /
# GET /jarvis/integrations/status) stay SUPERADMIN-only -- not tested here.

def _make_token(roles: list) -> str:
    """Build a signed JWT without touching the DB."""
    from api.routers.auth import create_access_token

    return create_access_token(
        {
            "user_id": f"test-{roles[0].lower()}-001",
            "username": f"test_{roles[0].lower()}",
            "roles": roles,
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )


def test_catalog_endpoint_denied_below_admin(app):
    """A role below ADMIN (SALES_STAFF) must get 403 on GET /integrations/catalog."""
    from fastapi.testclient import TestClient

    staff_token = _make_token(["SALES_STAFF"])
    client = TestClient(app)
    response = client.get(
        "/api/v1/settings/integrations/catalog",
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code in (403, 401), (
        f"Expected 403/401 for SALES_STAFF on catalog endpoint, got {response.status_code}"
    )


def test_catalog_endpoint_allows_admin(app):
    """ADMIN must be able to fetch the catalog (opened from SUPERADMIN-only)."""
    from fastapi.testclient import TestClient

    admin_token = _make_token(["ADMIN"])
    client = TestClient(app)
    response = client.get(
        "/api/v1/settings/integrations/catalog",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, (
        f"Expected 200 for ADMIN on catalog endpoint, got {response.status_code}"
    )
    body = response.json()
    assert "catalog" in body


def test_catalog_endpoint_allows_superadmin(app):
    """SUPERADMIN must be able to fetch the catalog."""
    from fastapi.testclient import TestClient

    superadmin_token = _make_token(["SUPERADMIN"])
    client = TestClient(app)
    response = client.get(
        "/api/v1/settings/integrations/catalog",
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "catalog" in body
    catalog_types = {entry["type"] for entry in body["catalog"]}
    assert "shopify" in catalog_types
    assert "anthropic" in catalog_types
    assert "storage" in catalog_types
    # gst-portal read-only info entry surfaces the manual GSTR-1/3B workflow.
    assert "gst-portal" in catalog_types


# ---------------------------------------------------------------------------
# 5. get_configured_agent_model resolver (DB -> env -> default)
# ---------------------------------------------------------------------------

def test_configured_agent_model_db_wins(monkeypatch):
    """A model picked in the UI (DB integration config) takes precedence."""
    monkeypatch.setenv("AGENT_CLAUDE_MODEL", "claude-haiku-4-5")
    import api.services.integration_config as ic
    monkeypatch.setattr(ic, "_load_db_config", lambda _type: {"model": "claude-opus-4-8"})
    assert ic.get_configured_agent_model() == "claude-opus-4-8"


def test_configured_agent_model_db_without_key(monkeypatch):
    """DB model is honoured even when no api_key is in the doc (resolver reads
    the raw DB doc, not get_anthropic_config which requires a key)."""
    monkeypatch.delenv("AGENT_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    import api.services.integration_config as ic
    monkeypatch.setattr(ic, "_load_db_config", lambda _type: {"model": "claude-sonnet-4-6"})
    assert ic.get_configured_agent_model() == "claude-sonnet-4-6"


def test_configured_agent_model_env_fallback(monkeypatch):
    """No DB model -> AGENT_CLAUDE_MODEL env, then JARVIS_MODEL legacy."""
    import api.services.integration_config as ic
    monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
    monkeypatch.delenv("AGENT_CLAUDE_MODEL", raising=False)
    monkeypatch.setenv("JARVIS_MODEL", "claude-legacy-x")
    assert ic.get_configured_agent_model() == "claude-legacy-x"
    monkeypatch.setenv("AGENT_CLAUDE_MODEL", "claude-haiku-4-5")
    assert ic.get_configured_agent_model() == "claude-haiku-4-5"


def test_configured_agent_model_default(monkeypatch):
    """No DB, no env -> curated current default."""
    import api.services.integration_config as ic
    monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
    monkeypatch.delenv("AGENT_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_MODEL", raising=False)
    assert ic.get_configured_agent_model() == ic.DEFAULT_AGENT_MODEL == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 6. Live Anthropic models endpoint (fail-soft fallback + RBAC gate)
# ---------------------------------------------------------------------------

def test_anthropic_models_fallback_when_no_key(app, monkeypatch):
    """No api_key + no live call -> curated fallback list, never a 500."""
    from fastapi.testclient import TestClient
    import api.routers.settings as settings_mod
    import api.services.integration_config as ic

    # No key configured (DB doc has no key, env empty) -> _fetch returns None
    # -> fallback path.
    monkeypatch.setattr(ic, "_load_db_config", lambda _type: {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    # Reset the in-process cache so this test is deterministic.
    settings_mod._ANTHROPIC_MODELS_CACHE["models"] = None
    settings_mod._ANTHROPIC_MODELS_CACHE["at"] = 0.0

    client = TestClient(app)
    resp = client.get(
        "/api/v1/settings/integrations/anthropic/models",
        headers={"Authorization": f"Bearer {_make_token(['ADMIN'])}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "fallback"
    ids = {m["id"] for m in body["models"]}
    assert {"claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"} <= ids
    for m in body["models"]:
        assert m.get("id") and m.get("display_name")


def test_anthropic_models_uses_live_list(app, monkeypatch):
    """When the live call returns models, the endpoint normalizes + serves them.

    Order-robust: mocks the stable httpx transport (which _fetch_anthropic_models
    resolves via `import httpx` at CALL time) instead of patching the settings-
    module function attribute. A prior test in the full suite can reload
    api.routers.settings, leaving the registered route bound to a stale module
    dict that a `setattr(settings_mod, "_fetch_anthropic_models", ...)` patch
    never reaches -- which is why the attribute-patch version passed in isolation
    but failed in the full suite. The httpx + get_anthropic_config patches are
    resolved at call time, so they hold regardless of any reload.
    """
    from fastapi.testclient import TestClient
    import httpx
    import api.routers.settings as settings_mod
    import api.services.integration_config as ic

    # Build the client BEFORE patching httpx so the TestClient transport is
    # unaffected (it binds httpx at import time; this patch only hits _fetch).
    client = TestClient(app)

    settings_mod._ANTHROPIC_MODELS_CACHE["models"] = None
    settings_mod._ANTHROPIC_MODELS_CACHE["at"] = 0.0

    # Endpoint gates the live call on a configured key; provide one. The route
    # does `from ..services.integration_config import get_anthropic_config` at
    # call time, so patching the current module is reload-proof.
    monkeypatch.setattr(ic, "get_anthropic_config", lambda: {"api_key": "sk-ant-x"})

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "claude-future-9", "display_name": "Claude Future 9"}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)

    resp = client.get(
        "/api/v1/settings/integrations/anthropic/models",
        headers={"Authorization": f"Bearer {_make_token(['SUPERADMIN'])}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] in ("live", "cache")
    assert any(m["id"] == "claude-future-9" for m in body["models"])
    # Clean up the cache so other tests aren't affected.
    settings_mod._ANTHROPIC_MODELS_CACHE["models"] = None
    settings_mod._ANTHROPIC_MODELS_CACHE["at"] = 0.0


def test_anthropic_models_denied_below_admin(app):
    """A role below ADMIN must be blocked from the model list."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get(
        "/api/v1/settings/integrations/anthropic/models",
        headers={"Authorization": f"Bearer {_make_token(['SALES_STAFF'])}"},
    )
    assert resp.status_code in (401, 403), resp.status_code
