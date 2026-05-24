"""
IMS 2.0 - Integration status reporter (read-only, KEYS ONLY)
============================================================
Builds a SUPERADMIN-facing report of which external integrations are
configured vs dormant, so the owner can see what's live as credentials
are added on Railway.

HARD RULE: this module reports the PRESENCE of credentials only - never a
value. For env vars it reports the KEY name plus a boolean. For the
`integrations` Mongo collection it reports which config FIELD NAMES are
populated (e.g. "key_id"), never their contents. No secret ever leaves
the process through this surface.

It deliberately reads `os.getenv` fresh each call so the report reflects
the current process environment. Note that the providers themselves read
several of these vars at module-import time, so a Railway variable change
takes effect for them on the next redeploy (Railway restarts on a
variable change, so the report and the providers converge after deploy).

ASCII only (Windows cp1252).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import os


# ----------------------------------------------------------------------------
# Integration registry - the single source of truth for what we report on.
# Each entry declares where its credentials come from and what is required.
# ----------------------------------------------------------------------------
#
# source:
#   "env"            - credentials are Railway env vars only
#   "env_or_collection" - env vars OR the integrations collection
#   "collection"     - credentials live only in the integrations collection
#                      (configured via POST /api/v1/admin/integrations/<type>)
#   "export_only"    - works without external creds (file/XML export)
#   "not_wired"      - no live connector exists yet (build item)
#
# dispatch_gated: True when the integration's *live action* is gated behind
#   DISPATCH_MODE=live (outbound writes / bookings / sends). Read-only
#   integrations are not gated.

_REGISTRY: List[Dict[str, Any]] = [
    {
        "id": "anthropic",
        "label": "Anthropic / Claude",
        "powers": "ORACLE narratives, JARVIS & CORTEX chat, agent copy",
        "source": "env",
        "env_required": ["ANTHROPIC_API_KEY"],
        "env_optional": ["AGENT_CLAUDE_MODEL", "ANTHROPIC_API_URL", "LLM_DEFAULT_MODEL"],
        "dispatch_gated": False,
    },
    {
        "id": "msg91_whatsapp",
        "label": "MSG91 WhatsApp",
        "powers": "Rx-expiry / birthday / follow-up / order / task WhatsApp alerts (MEGAPHONE)",
        "source": "env",
        "env_required": ["MSG91_API_KEY", "MSG91_WHATSAPP_INTEGRATED_NUMBER"],
        "env_optional": ["MSG91_WHATSAPP_NAMESPACE"],
        "dispatch_gated": True,
    },
    {
        "id": "msg91_sms",
        "label": "MSG91 SMS",
        "powers": "DLT transactional SMS fallback (MEGAPHONE)",
        "source": "env",
        "env_required": ["MSG91_API_KEY", "MSG91_SMS_TEMPLATE_ID"],
        "env_optional": ["MSG91_SENDER"],
        "dispatch_gated": True,
    },
    {
        "id": "shiprocket",
        "label": "Shiprocket",
        "powers": "Book + track customer shipments (Orders page); NEXUS auto-tracking",
        "source": "env_or_collection",
        "env_required": ["SHIPROCKET_EMAIL", "SHIPROCKET_PASSWORD"],
        "env_optional": ["SHIPROCKET_PICKUP_LOCATION"],
        "collection_type": "shiprocket",
        "collection_required": ["email", "password"],
        "dispatch_gated": True,
    },
    {
        "id": "pagespeed",
        "label": "Google PageSpeed (PIXEL)",
        "powers": "PIXEL Lighthouse / accessibility audits",
        "source": "env",
        "env_required": ["PAGESPEED_API_KEY"],
        "env_optional": ["FRONTEND_BASE_URL"],
        "dispatch_gated": False,
    },
    {
        "id": "razorpay",
        "label": "Razorpay",
        "powers": "Payment reconciliation + webhooks (NEXUS)",
        "source": "collection",
        "collection_type": "razorpay",
        "collection_required": ["key_id", "key_secret"],
        "collection_optional": ["webhook_secret"],
        "dispatch_gated": False,
    },
    {
        "id": "shopify",
        "label": "Shopify",
        "powers": "Catalog push / order pull (NEXUS)",
        "source": "collection",
        "collection_type": "shopify",
        "collection_required": ["shop_url", "access_token"],
        "dispatch_gated": True,
    },
    {
        "id": "tally",
        "label": "Tally ERP9",
        "powers": "Nightly sales-voucher XML export (download for CA)",
        "source": "export_only",
        "dispatch_gated": False,
        "notes": "XML export works without credentials. Live HTTP push to a Tally server is not wired.",
    },
    {
        "id": "gst_portal",
        "label": "GST Portal / GSP filing",
        "powers": "GSTR-1 / GSTR-3B e-filing",
        "source": "not_wired",
        "dispatch_gated": False,
        "notes": "Exports offline-tool JSON only. Live e-filing needs a licensed GSP (build item).",
    },
]


def _dispatch_mode() -> str:
    """Current DISPATCH_MODE, read fresh. off | test | live (default off)."""
    return (os.getenv("DISPATCH_MODE", "off") or "off").strip().lower()


def _env_key_report(keys: List[str]) -> List[Dict[str, Any]]:
    """[{key, present}] for the given env var names. KEYS ONLY - never values."""
    return [{"key": k, "present": bool(os.getenv(k))} for k in keys]


def _load_collection_doc(db, integration_type: str) -> Optional[Dict[str, Any]]:
    """Read the canonical {type:<lower>} integrations doc, or None. Fail-soft."""
    if db is None or not integration_type:
        return None
    try:
        coll = db.get_collection("integrations")
        return coll.find_one({"type": integration_type.lower()})
    except Exception:
        return None


def _present_config_keys(config: Dict[str, Any], candidate_keys: List[str]) -> List[str]:
    """Subset of candidate_keys that are populated (truthy) in config.
    Returns FIELD NAMES only - never the values."""
    if not isinstance(config, dict):
        return []
    present: List[str] = []
    for k in candidate_keys:
        val = config.get(k)
        if isinstance(val, str):
            if val.strip():
                present.append(k)
        elif val:
            present.append(k)
    return present


def _build_one(entry: Dict[str, Any], db) -> Dict[str, Any]:
    source = entry["source"]
    dispatch_gated = bool(entry.get("dispatch_gated"))
    mode = _dispatch_mode()

    out: Dict[str, Any] = {
        "id": entry["id"],
        "label": entry["label"],
        "powers": entry["powers"],
        "source": source,
        "dispatch_gated": dispatch_gated,
        "env_keys": [],
        "collection": None,
        "configured": False,
        "state": "dormant",
        "notes": entry.get("notes", ""),
    }

    # --- env side -----------------------------------------------------------
    env_required = entry.get("env_required", [])
    env_optional = entry.get("env_optional", [])
    if env_required or env_optional:
        out["env_keys"] = _env_key_report(env_required + env_optional)
    env_configured = bool(env_required) and all(bool(os.getenv(k)) for k in env_required)

    # --- collection side ----------------------------------------------------
    coll_configured = False
    coll_required = entry.get("collection_required", [])
    coll_optional = entry.get("collection_optional", [])
    if entry.get("collection_type"):
        doc = _load_collection_doc(db, entry["collection_type"])
        if doc is not None:
            config = doc.get("config") or {}
            present_required = _present_config_keys(config, coll_required)
            present_optional = _present_config_keys(config, coll_optional)
            missing_required = [k for k in coll_required if k not in present_required]
            enabled = bool(doc.get("enabled"))
            out["collection"] = {
                "exists": True,
                "enabled": enabled,
                "present_keys": present_required + present_optional,
                "missing_required": missing_required,
            }
            coll_configured = enabled and not missing_required
        else:
            out["collection"] = {
                "exists": False,
                "enabled": False,
                "present_keys": [],
                "missing_required": list(coll_required),
            }

    # --- resolve configured + state ----------------------------------------
    if source == "not_wired":
        out["state"] = "not_wired"
        out["configured"] = False
        return out
    if source == "export_only":
        out["state"] = "export_only"
        out["configured"] = True
        return out

    if source == "env":
        configured = env_configured
    elif source == "collection":
        configured = coll_configured
    else:  # env_or_collection
        configured = env_configured or coll_configured
    out["configured"] = configured

    if not configured:
        out["state"] = "dormant"
    elif not dispatch_gated:
        out["state"] = "active"  # read-only, runs as soon as creds exist
    else:
        if mode == "live":
            out["state"] = "live"
        elif mode == "test":
            # Only MSG91 honors an allowlist in test mode; for the others
            # non-live still means simulated. We surface "test_only" only for
            # the MSG91 channels and "simulated" for the rest so the label is
            # accurate.
            out["state"] = "test_only" if entry["id"].startswith("msg91") else "simulated"
        else:
            out["state"] = "simulated"

    return out


def build_integration_status(db=None) -> Dict[str, Any]:
    """Full read-only integration status report. KEYS ONLY, never values.

    Returns:
        {
          generated_at, dispatch_mode,
          summary: {total, configured, live},
          test_phone_set: bool,         # whether TEST_PHONE is set (KEY presence only)
          integrations: [ {id, label, powers, source, dispatch_gated,
                           env_keys:[{key,present}], collection:{...}|None,
                           configured, state, notes}, ... ],
        }
    """
    mode = _dispatch_mode()
    items = [_build_one(entry, db) for entry in _REGISTRY]
    configured_count = sum(1 for i in items if i["configured"])
    live_count = sum(1 for i in items if i["state"] in ("live", "active"))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dispatch_mode": mode,
        "test_phone_set": bool(os.getenv("TEST_PHONE")),
        "summary": {
            "total": len(items),
            "configured": configured_count,
            "live": live_count,
        },
        "integrations": items,
    }
