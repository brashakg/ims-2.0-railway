"""
IMS 2.0 - Shared integration config loader
==========================================
Single place for reading an integration's decrypted config from the Mongo
`integrations` collection, with an env-var fallback for each provider.

Pattern (identical for every service):
  1. Try the DB first (reads via `_load_db_config`).
  2. Fall back to env vars when the DB doc is absent/disabled.
  3. Return {} when neither source is usable.

This wires up "Settings -> Integrations" for photoroom, s3, and anthropic so
the owner never has to touch Railway env vars for day-to-day credential
rotation.

NO secrets are logged here.  The decrypt helper in routers.settings is
re-used so the encryption scheme stays in one place.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _load_db_config(integration_type: str) -> Dict[str, Any]:
    """Return the decrypted config dict for `integration_type`, or {} when
    the record is absent, disabled, or the DB is unavailable.

    This intentionally mirrors nexus_providers._load_integration_config but
    imports the decryption logic from routers.settings so we only maintain
    one encryption implementation.
    """
    try:
        from database.connection import get_db

        db = get_db()
        if not (db and db.is_connected):
            return {}
        coll = db.get_collection("integrations")
        doc = coll.find_one({"type": integration_type.lower(), "enabled": True})
        if not doc:
            return {}
        raw_cfg = doc.get("config") or {}
        if not isinstance(raw_cfg, dict):
            return {}
        # Re-use the decrypt helper from routers.settings (single implementation).
        try:
            from api.routers.settings import _decrypt_config

            return _decrypt_config(raw_cfg)
        except Exception:
            # If the decrypt import fails (e.g. isolated test), return raw.
            return raw_cfg
    except Exception as exc:
        logger.debug("[integration_config] DB read failed for %s: %s", integration_type, exc)
        return {}


# ---------------------------------------------------------------------------
# Per-provider loaders (DB first, then env fallback)
# ---------------------------------------------------------------------------


def get_photoroom_config() -> Dict[str, Any]:
    """Return Photoroom config: {api_key, provider} or {} when unconfigured.

    DB key  : type="photoroom"  -> config.api_key
    Env vars: PHOTOROOM_API_KEY (legacy/override)
    """
    cfg = _load_db_config("photoroom")
    api_key = cfg.get("api_key") or os.getenv("PHOTOROOM_API_KEY", "")
    provider = cfg.get("provider") or os.getenv("IMAGE_EDIT_PROVIDER", "")
    if api_key:
        return {"api_key": api_key, "provider": provider or "photoroom"}
    return {}


def get_storage_config() -> Dict[str, Any]:
    """Return S3/storage config, merging DB over env.

    DB key  : type="storage"
    Env vars: IMAGE_STORAGE_PROVIDER, IMAGE_S3_BUCKET, IMAGE_S3_ACCESS_KEY,
              IMAGE_S3_SECRET_KEY, IMAGE_S3_ENDPOINT, IMAGE_S3_PUBLIC_BASE,
              IMAGE_S3_REGION
    """
    cfg = _load_db_config("storage")
    return {
        "provider": cfg.get("provider") or os.getenv("IMAGE_STORAGE_PROVIDER", ""),
        "bucket": cfg.get("bucket") or os.getenv("IMAGE_S3_BUCKET", ""),
        "access_key": cfg.get("access_key") or os.getenv("IMAGE_S3_ACCESS_KEY", ""),
        "secret_key": cfg.get("secret_key") or os.getenv("IMAGE_S3_SECRET_KEY", ""),
        "endpoint": cfg.get("endpoint") or os.getenv("IMAGE_S3_ENDPOINT", ""),
        "public_base": cfg.get("public_base") or os.getenv("IMAGE_S3_PUBLIC_BASE", ""),
        "region": cfg.get("region") or os.getenv("IMAGE_S3_REGION", ""),
    }


def get_anthropic_config() -> Dict[str, Any]:
    """Return Anthropic/Claude config.

    DB key  : type="anthropic"  -> config.api_key, config.model
    Env vars: ANTHROPIC_API_KEY, AGENT_CLAUDE_MODEL
    """
    cfg = _load_db_config("anthropic")
    api_key = cfg.get("api_key") or os.getenv("ANTHROPIC_API_KEY", "")
    model = cfg.get("model") or os.getenv("AGENT_CLAUDE_MODEL", "claude-haiku-4-5")
    if api_key:
        return {"api_key": api_key, "model": model}
    return {}


# Default model used when nothing is configured. Kept as a single constant so
# the resolver, the live-models fallback list, and any caller agree on one
# current, non-retired model id.
DEFAULT_AGENT_MODEL = "claude-sonnet-4-6"


def get_configured_agent_model() -> str:
    """Single source of truth for the main agent (JARVIS/CORTEX/ORACLE) model.

    Resolution order (read FRESH at call time so a UI selection in
    Settings -> Integrations takes effect WITHOUT a redeploy):
      1. DB integration config `model` (Settings -> Integrations -> Anthropic).
      2. Env override AGENT_CLAUDE_MODEL, then JARVIS_MODEL (legacy).
      3. Curated current default DEFAULT_AGENT_MODEL.

    The DB read goes through get_anthropic_config(), which only returns a
    config when an api_key is present; we therefore read the raw DB doc
    directly here so a model picked in the UI is honoured even before the key
    is saved in the same doc. Fail-soft: any error -> env/default.
    """
    # 1. DB-configured model (does not require an api_key to be set).
    try:
        cfg = _load_db_config("anthropic")
        model = cfg.get("model")
        if model:
            return str(model).strip()
    except Exception:  # noqa: BLE001 - never block an agent on config read
        pass
    # 2. Env override (AGENT_CLAUDE_MODEL preferred; JARVIS_MODEL legacy).
    env_model = os.getenv("AGENT_CLAUDE_MODEL") or os.getenv("JARVIS_MODEL")
    if env_model:
        return env_model.strip()
    # 3. Curated current default.
    return DEFAULT_AGENT_MODEL


def get_whatsapp_config() -> Dict[str, Any]:
    """Return inbound-WhatsApp (Meta WABA) config.

    DB key  : type="meta_whatsapp" -> config.verify_token, app_secret,
              phone_number_id, access_token, default_store_id
    Env vars: WABA_VERIFY_TOKEN, WABA_APP_SECRET, WABA_DEFAULT_STORE_ID
    Read fresh per-request so a Save in the Settings -> Integrations hub takes
    effect without a redeploy. Fail-soft (DB absent -> env-only).
    """
    cfg = _load_db_config("meta_whatsapp")
    return {
        "verify_token": cfg.get("verify_token") or os.getenv("WABA_VERIFY_TOKEN", ""),
        "app_secret": cfg.get("app_secret") or os.getenv("WABA_APP_SECRET", ""),
        "phone_number_id": cfg.get("phone_number_id")
        or os.getenv("WABA_PHONE_NUMBER_ID", ""),
        "access_token": cfg.get("access_token") or os.getenv("WABA_ACCESS_TOKEN", ""),
        "default_store_id": cfg.get("default_store_id")
        or os.getenv("WABA_DEFAULT_STORE_ID", "HQ"),
    }
