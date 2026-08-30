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

It reads env vars and the `integrations` collection fresh each call so the
report reflects the current process environment. ONE deliberate exception:
`_dispatch_mode()` below returns agents.providers.dispatch_mode() instead of
re-reading DISPATCH_MODE, because the gate's snapshot - not the env - is what
a send is gated on. Re-reading it here made this screen report "live" for
padded values the gate refused, e.g. DISPATCH_MODE=" live" sent nothing.

The credential-resolution ORDER declared here (collection first, then env) is
the order api.services.integration_config uses, and that correspondence is
asserted in tests/test_integration_credentials_wiring.py. It is NOT enforced
by construction: the table below is a hand-kept copy of each provider's env
keys and config field names, so a provider that renames a field can drift
from this report without anything failing. Do not read this paragraph as a
guarantee that the two cannot disagree - only the send gate is single-sourced.

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
        "env_optional": [
            "AGENT_CLAUDE_MODEL",
            "ANTHROPIC_API_URL",
            "LLM_DEFAULT_MODEL",
        ],
        "dispatch_gated": False,
    },
    {
        "id": "msg91_whatsapp",
        "label": "MSG91 WhatsApp",
        "powers": "Rx-expiry / birthday / follow-up / order / task WhatsApp alerts (MEGAPHONE)",
        "source": "env_or_collection",
        "env_required": ["MSG91_API_KEY", "MSG91_WHATSAPP_INTEGRATED_NUMBER"],
        "env_optional": ["MSG91_WHATSAPP_NAMESPACE"],
        "collection_type": "whatsapp",
        "collection_required": ["api_key", "whatsapp_number"],
        "collection_optional": ["sms_template_id", "sender"],
        "dispatch_gated": True,
    },
    {
        "id": "msg91_sms",
        "label": "MSG91 SMS",
        "powers": "DLT transactional SMS fallback (MEGAPHONE)",
        "source": "env_or_collection",
        "env_required": ["MSG91_API_KEY", "MSG91_SMS_TEMPLATE_ID"],
        "env_optional": ["MSG91_SENDER"],
        "collection_type": "whatsapp",
        "collection_required": ["api_key", "sms_template_id"],
        "collection_optional": ["sender"],
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
        "source": "env_or_collection",
        "env_required": ["PAGESPEED_API_KEY"],
        "env_optional": ["FRONTEND_BASE_URL"],
        "collection_type": "pagespeed",
        "collection_required": ["api_key"],
        "dispatch_gated": False,
    },
    {
        "id": "slack",
        "label": "Slack",
        "powers": "CRITICAL / HIGH anomaly alerts raised by ORACLE",
        "source": "env_or_collection",
        "env_required": ["SLACK_WEBHOOK_URL"],
        "env_optional": ["SLACK_ALERT_SEVERITY"],
        "collection_type": "slack",
        "collection_required": ["webhook_url"],
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
    """The send gate, as agents.providers resolved it -- off | test | live.

    NOT a re-read of DISPATCH_MODE. This screen tells the owner whether live
    messaging is armed, and the only thing that arms it is the value
    agents.providers captured; re-parsing the env here answered a different
    question and disagreed with the gate on every whitespace-padded value
    (`DISPATCH_MODE=" live"` -> this said "live", the gate sent nothing).
    """
    from agents.providers import dispatch_mode

    return dispatch_mode()


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


def _present_config_keys(
    config: Dict[str, Any], candidate_keys: List[str]
) -> List[str]:
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
    env_configured = bool(env_required) and all(
        bool(os.getenv(k)) for k in env_required
    )

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
            out["state"] = (
                "test_only" if entry["id"].startswith("msg91") else "simulated"
            )
        else:
            out["state"] = "simulated"

    return out


def _preflight_row(
    row_id: str, label: str, ok: bool, detail: str, next_step: str = ""
) -> Dict[str, Any]:
    return {
        "id": row_id,
        "label": label,
        "ok": bool(ok),
        "detail": detail,
        "next_step": next_step,
    }


def build_messaging_preflight(db=None) -> Dict[str, Any]:
    """MSG91 + Coexistence messaging preflight: every row honest, with the
    owner's NEXT STEP named. Reports presence/counts/ids only -- never a
    credential value and never a phone number. Fail-soft throughout."""
    from api.services.integration_config import get_msg91_config
    from api.services.notification_templates import wa_registry_report

    try:
        cfg = get_msg91_config()
    except Exception:
        cfg = {}
    mode = _dispatch_mode()
    rows: List[Dict[str, Any]] = []

    # 1. Credentials
    api_key_ok = bool(cfg.get("api_key"))
    rows.append(_preflight_row(
        "creds", "MSG91 auth key",
        api_key_ok,
        "auth key present" if api_key_ok else "no MSG91 auth key anywhere",
        "" if api_key_ok else "Paste the MSG91 auth key under Settings -> "
        "Integrations -> WhatsApp Business (MSG91), or set MSG91_API_KEY on Railway.",
    ))

    # 2. Default sender number
    default_ok = bool(cfg.get("whatsapp_number"))
    rows.append(_preflight_row(
        "default_number", "Default WhatsApp number",
        default_ok,
        "default integrated number set" if default_ok
        else "no default WhatsApp integrated number",
        "" if default_ok else "Enter the WhatsApp Integrated Number in the "
        "same tile (or MSG91_WHATSAPP_INTEGRATED_NUMBER) - it is the fallback "
        "sender for stores without their own mapped number.",
    ))

    # 3. Per-store Coexistence numbers
    store_map = cfg.get("store_numbers") or {}
    store_ids: List[str] = []
    stores_readable = False
    try:
        coll = db.get_collection("stores") if db is not None else None
        if coll is not None:
            store_ids = [
                s.get("store_id")
                for s in coll.find(
                    {"is_active": {"$ne": False}}, {"_id": 0, "store_id": 1}
                )
                if s.get("store_id")
            ]
            stores_readable = True
    except Exception:
        stores_readable = False
    missing_stores = [s for s in store_ids if s not in store_map]
    if not stores_readable:
        rows.append(_preflight_row(
            "store_numbers", "Per-store WhatsApp numbers",
            False,
            f"{len(store_map)} store(s) mapped; could not read the stores list "
            "to check coverage",
            "Open this screen with the database reachable to verify every "
            "store is mapped.",
        ))
    else:
        rows.append(_preflight_row(
            "store_numbers", "Per-store WhatsApp numbers",
            bool(store_ids) and not missing_stores,
            f"{len(store_ids) - len(missing_stores)} of {len(store_ids)} active "
            "stores mapped"
            + (f"; missing: {', '.join(missing_stores)}" if missing_stores else ""),
            "" if not missing_stores and store_ids else
            "Map each shop's own WhatsApp number in Settings -> Integrations -> "
            "WhatsApp Business (MSG91) -> 'Per-store WhatsApp numbers' "
            "(STORE-ID:number, comma-separated). Unmapped stores send from the "
            "default number.",
        ))

    # 4. Template registry
    try:
        registry = wa_registry_report()
    except Exception:
        registry = {}
    seed_defaults = sorted(k for k, v in registry.items() if v.get("seed_default"))
    mapped_count = len(registry) - len(seed_defaults)
    rows.append(_preflight_row(
        "templates", "WhatsApp templates mapped",
        bool(registry) and not seed_defaults,
        f"{mapped_count} of {len(registry)} flows carry an owner-mapped, "
        "approved template name"
        + (f"; still on seed defaults: {', '.join(seed_defaults)}"
           if seed_defaults else ""),
        "" if not seed_defaults else "Once MSG91 approves each WhatsApp "
        "template, enter its approved name against the flow under Settings -> "
        "Notifications -> Templates. Flows on seed-default names will be "
        "rejected by MSG91 when live.",
    ))

    # 5. DLT ids
    pe_id_ok = bool(os.getenv("DLT_PE_ID") or os.getenv("MSG91_DLT_PE_ID"))
    sms_tpl_ok = bool(cfg.get("sms_template_id"))
    dlt_detail = []
    dlt_detail.append("DLT PE id set" if pe_id_ok else "DLT PE id missing")
    dlt_detail.append(
        "SMS DLT template id set" if sms_tpl_ok else "SMS DLT template id missing"
    )
    rows.append(_preflight_row(
        "dlt", "DLT registration ids",
        pe_id_ok and sms_tpl_ok,
        "; ".join(dlt_detail),
        "" if (pe_id_ok and sms_tpl_ok) else "After DLT entity registration, "
        "set DLT_PE_ID on Railway and the SMS Template ID in the WhatsApp "
        "Business (MSG91) tile.",
    ))

    # 6. Dispatch mode (the send gate -- env-only, on purpose)
    rows.append(_preflight_row(
        "dispatch_mode", "Sending mode (DISPATCH_MODE)",
        mode in ("test", "live"),
        f"DISPATCH_MODE={mode}"
        + ("" if mode in ("test", "live") else " - nothing is sent to anyone"),
        "" if mode == "live" else "When ready to arm: set DISPATCH_MODE=test "
        "with TEST_PHONE on Railway so every flow reaches your own phone "
        "first, then DISPATCH_MODE=live. This switch is env-only by design.",
    ))

    # 7. Test phone
    test_phone_ok = bool(os.getenv("TEST_PHONE"))
    rows.append(_preflight_row(
        "test_phone", "TEST_PHONE",
        test_phone_ok,
        "TEST_PHONE set" if test_phone_ok else "TEST_PHONE not set",
        "" if test_phone_ok else "Set TEST_PHONE on Railway (your own number) "
        "- required before DISPATCH_MODE=test can deliver anything.",
    ))

    # 8. Delivery reports (the message_events spine). Counts only -- never a
    # number or a name. A dark deploy has zero events and stays ok=True; the
    # row turns red only on REAL failures reported by MSG91 in the window.
    try:
        from api.services.message_events import failure_counts

        fc = failure_counts(days=7, db=db)
    except Exception:  # noqa: BLE001
        fc = None
    if fc is None:
        rows.append(_preflight_row(
            "delivery_failures", "Delivery failures (7 days)",
            False,
            "could not read message_events",
            "Open this screen with the database reachable.",
        ))
    else:
        failed, total = fc["failed"], fc["total"]
        if total == 0:
            detail = (
                "no delivery reports in the last 7 days (MSG91 posts them to "
                "/api/v1/integrations/msg91/webhooks/<channel> once armed)"
            )
        else:
            detail = f"{failed} failed of {total} delivery events in the last 7 days"
        rows.append(_preflight_row(
            "delivery_failures", "Delivery failures (7 days)",
            failed == 0,
            detail,
            "" if failed == 0 else "Open the affected customers' Customer 360 "
            "message timelines to see which flows failed; a dead WhatsApp "
            "number is the usual cause (SMS fallback is the next build item).",
        ))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "ok": all(r["ok"] for r in rows),
    }


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

    try:
        preflight = build_messaging_preflight(db)
    except Exception:  # noqa: BLE001 - the preflight must never sink the report
        preflight = {"rows": [], "ok": False}

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
        "messaging_preflight": preflight,
    }
