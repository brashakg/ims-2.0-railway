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
# 4. SUPERADMIN gate on catalog endpoint
# ---------------------------------------------------------------------------

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


def test_catalog_endpoint_requires_superadmin(app):
    """Non-superadmin (ADMIN role) must get 403 on GET /integrations/catalog."""
    from fastapi.testclient import TestClient

    admin_token = _make_token(["ADMIN"])
    client = TestClient(app)
    response = client.get(
        "/api/v1/settings/integrations/catalog",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # ADMIN should be denied; SUPERADMIN is required
    assert response.status_code in (403, 401), (
        f"Expected 403/401 for ADMIN on catalog endpoint, got {response.status_code}"
    )


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
