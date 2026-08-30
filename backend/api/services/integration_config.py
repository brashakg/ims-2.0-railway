"""
IMS 2.0 - Shared integration config loader
==========================================
Single place for reading an integration's decrypted config from the Mongo
`integrations` collection, with an env-var fallback for each provider.

Pattern (identical for every service):
  1. Try the DB first (reads via `_load_db_config`).
  2. Fall back to env vars when the DB doc is absent/disabled.
  3. Return {} when neither source is usable.

This wires up "Settings -> Integrations" for photoroom, s3, anthropic and the
messaging/alerting providers (MSG91, Slack, PageSpeed) so the owner never has
to touch Railway env vars for day-to-day credential rotation.

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


def _parse_store_numbers(raw: Any) -> Dict[str, str]:
    """Parse the per-store WhatsApp sender map (Coexistence).

    Accepts an already-shaped dict, or the Settings text format
    "STORE-ID:919812345678, STORE-2:919887654321" -- comma-separated
    store_id:number pairs. Fragments without a colon, or with an empty
    side, are skipped (fail-soft); ids and numbers are stripped.
    Returns {} when nothing usable.
    """
    if isinstance(raw, dict):
        return {
            str(k).strip(): str(v).strip()
            for k, v in raw.items()
            if str(k).strip() and str(v).strip()
        }
    out: Dict[str, str] = {}
    for pair in str(raw or "").split(","):
        if ":" not in pair:
            continue
        sid, num = pair.split(":", 1)
        sid, num = sid.strip(), num.strip()
        if sid and num:
            out[sid] = num
    return out


def get_msg91_config() -> Dict[str, Any]:
    """Return MSG91 messaging credentials (WhatsApp Business + transactional SMS).

    DB key  : type="whatsapp" -> config.api_key, config.whatsapp_number,
              config.sms_template_id, config.sender, config.store_numbers
              (Settings -> Integrations -> "WhatsApp Business (MSG91)")
    Env vars: MSG91_API_KEY, MSG91_WHATSAPP_INTEGRATED_NUMBER,
              MSG91_SMS_TEMPLATE_ID, MSG91_SENDER, MSG91_STORE_NUMBERS

    Read FRESH per send so a Save in the hub takes effect without a redeploy.
    Fail-soft: DB absent/disabled -> env-only, exactly as before.

    store_numbers is the Coexistence per-store sender map, parsed to
    {store_id: integrated_number}. Numbers are not secrets, but they live in
    the same encrypted-at-rest integrations doc as everything else here.

    BOUNDARY: this resolves CREDENTIALS only. Whether IMS is allowed to send
    at all stays with DISPATCH_MODE, which is env-only by design and is NEVER
    read from the database. Do not add a dispatch/enable switch here.
    """
    cfg = _load_db_config("whatsapp")
    return {
        "api_key": cfg.get("api_key") or os.getenv("MSG91_API_KEY", ""),
        "whatsapp_number": cfg.get("whatsapp_number")
        or os.getenv("MSG91_WHATSAPP_INTEGRATED_NUMBER", ""),
        "sms_template_id": cfg.get("sms_template_id")
        or os.getenv("MSG91_SMS_TEMPLATE_ID", ""),
        # DLT-registered sender ID; BVOPTL is the owner's registered header.
        "sender": cfg.get("sender") or os.getenv("MSG91_SENDER", "") or "BVOPTL",
        "store_numbers": _parse_store_numbers(
            cfg.get("store_numbers") or os.getenv("MSG91_STORE_NUMBERS", "")
        ),
    }


def resolve_whatsapp_sender(store_id: Any = None) -> str:
    """THE one store -> WhatsApp-sender resolution (Meta Coexistence).

    Under Coexistence each shop's own WhatsApp Business number runs on the
    app AND the API, so IMS must send from the SHOP's number, not one shared
    number. Resolution:
      1. store_id given AND mapped in store_numbers -> that shop's number.
      2. otherwise -> the single default number (Settings tile first, then
         MSG91_WHATSAPP_INTEGRATED_NUMBER) -- a single-number or unarmed
         deployment behaves exactly as before.
    Returns "" when nothing is configured (the send door then fails honestly).

    Do NOT re-implement this mapping anywhere else; every WhatsApp send goes
    through agents.providers.send_whatsapp, which calls this.
    """
    cfg = get_msg91_config()
    if store_id:
        mapped = (cfg.get("store_numbers") or {}).get(str(store_id).strip())
        if mapped:
            return mapped
    return cfg.get("whatsapp_number") or ""


def get_slack_config() -> Dict[str, Any]:
    """Return the Slack incoming-webhook URL used for ORACLE anomaly alerts.

    DB key  : type="slack" -> config.webhook_url
    Env vars: SLACK_WEBHOOK_URL

    Returns {"webhook_url": ""} when nothing is configured, which keeps
    notify_slack a silent no-op. NO secrets logged.
    """
    cfg = _load_db_config("slack")
    url = cfg.get("webhook_url") or os.getenv("SLACK_WEBHOOK_URL", "")
    return {"webhook_url": str(url or "").strip()}


def get_pagespeed_config() -> Dict[str, Any]:
    """Return the Google PageSpeed API key used by the PIXEL agent.

    DB key  : type="pagespeed" -> config.api_key
    Env vars: PAGESPEED_API_KEY

    Returns {"api_key": ""} when unconfigured; PIXEL then stays on its
    heartbeat-only path instead of calling PageSpeed.
    """
    cfg = _load_db_config("pagespeed")
    return {"api_key": cfg.get("api_key") or os.getenv("PAGESPEED_API_KEY", "")}
