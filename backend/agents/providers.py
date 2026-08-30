"""
IMS 2.0 — Outbound notification providers (WhatsApp + SMS)
=============================================================
Thin async clients for the Indian messaging providers IMS 2.0 uses:

- MSG91 (default): WhatsApp Business + Transactional SMS + OTP. Single vendor,
  single API key, DLT-compliant for Indian telecom regulations.
- Twilio (optional): international SMS fallback. Not wired in Phase 4.2.

Credentials: resolved per send from Settings -> Integrations -> "WhatsApp
Business (MSG91)" first, falling back to the MSG91_* env vars. Saving on the
screen therefore takes effect immediately, with no redeploy. Note that this
covers credentials ONLY -- DISPATCH_MODE below is env-only and is the sole
switch that decides whether anything is actually sent.

Design mirrors claude_client.py:
- Fail soft — missing API keys / timeouts / non-200s return a failure tuple,
  they never raise. MEGAPHONE's drain loop must not die on one bad message.
- OPT-IN via env: DISPATCH_MODE env decides whether to actually send.
    DISPATCH_MODE=off   (default) — log only, never hit external APIs.
                                     Returns SIMULATED success so the drain
                                     loop can still mark messages SENT and
                                     we observe the drain cadence in staging.
    DISPATCH_MODE=test  — only send to the TEST_PHONE env var (safety for
                                     UAT deploys with real provider credentials).
    DISPATCH_MODE=live  — actually send to every number. Production.
  This prevents a misconfigured deploy from dumping 1000 messages on
  real customers the first time it touches MSG91.

Phase 4.2 wiring: MEGAPHONE calls `send_whatsapp()` / `send_sms()` once per
PENDING notification during its drain pass. Provider responses flow back as
status updates + provider_message_id on the notification_log doc.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import logging
import os

import httpx

logger = logging.getLogger(__name__)


# ============================================================================
# Env config
# ============================================================================

# off | test | live. ONE padding policy for every dispatch gate in the app:
# STRIP. A Railway variable pasted with a stray space must not silently disarm
# all messaging -- and SHOPIFY_DISPATCH_MODE (nexus_providers) already
# stripped, so `" live"` used to fire a real Shopify write while arming
# nothing here. Pinned by tests/test_integration_credentials_wiring.py ::
# test_a_padded_live_from_the_environment_still_arms_the_gate.
DISPATCH_MODE = os.getenv("DISPATCH_MODE", "off").strip().lower()
TEST_PHONE = os.getenv("TEST_PHONE", "")

# MSG91 credentials are NOT read here. They are resolved per send by
# _msg91() below -- Settings -> Integrations -> "WhatsApp Business (MSG91)"
# first, then the MSG91_* env vars -- so a credential saved on the screen
# takes effect without a redeploy. See api/services/integration_config.py.
_MSG91_DEFAULT_HOST = "api.msg91.com"  # MSG91 doc-canonical hostname (2026).
# `control.msg91.com` is the legacy alias and still serves the same endpoints;
# we accept either via env override so an environment with an older MSG91
# allowlist (firewall, IP rules) can keep using control.msg91.com without a
# code change.
MSG91_BASE_URL = (
    os.getenv("MSG91_BASE_URL") or f"https://{_MSG91_DEFAULT_HOST}/api/v5"
).rstrip("/")

PROVIDER_TIMEOUT = float(os.getenv("PROVIDER_TIMEOUT", "15.0"))


def _msg91() -> dict:
    """MSG91 credentials, resolved FRESH on every call.

    Returns {api_key, whatsapp_number, sms_template_id, sender}. The screen
    (Settings -> Integrations) wins; the MSG91_* env vars remain the fallback
    for a deployment that never touches the database.

    These are CREDENTIALS only. DISPATCH_MODE -- the switch that decides
    whether IMS is allowed to send at all -- stays env-only and is read
    above; nothing here can turn sending on.
    """
    from api.services.integration_config import get_msg91_config

    return get_msg91_config()


# ============================================================================
# Response envelope
# ============================================================================


@dataclass
class DispatchResult:
    """Standard result from any provider send call."""
    ok: bool
    status: str  # SENT | FAILED | SIMULATED | SKIPPED
    provider_id: Optional[str] = None  # vendor's message ID for tracing
    error: Optional[str] = None
    channel: Optional[str] = None
    dispatched_at: str = ""
    # WhatsApp only: the resolved template + sender the send used (or WOULD
    # have used -- the SIMULATED path fills this too, so a dark deploy can
    # prove the payload shape end to end without touching MSG91). Contains
    # no credential; never persisted by the drain.
    meta: Optional[dict] = None

    def __post_init__(self):
        if not self.dispatched_at:
            self.dispatched_at = datetime.now(timezone.utc).isoformat()


# ============================================================================
# Dispatch gate
# ============================================================================


def _should_dispatch(phone: str) -> tuple[bool, str]:
    """
    Central switch. Returns (should_dispatch, reason).
    reason is a human-readable string explaining why dispatch was suppressed.
    """
    if DISPATCH_MODE == "off":
        return False, f"DISPATCH_MODE=off — staging / dry-run; not sending to {phone[-4:]}"
    if DISPATCH_MODE == "test":
        if not TEST_PHONE:
            return False, "DISPATCH_MODE=test but TEST_PHONE unset"
        if phone.strip().replace("+", "").replace(" ", "") != TEST_PHONE.strip().replace("+", "").replace(" ", ""):
            return False, f"DISPATCH_MODE=test — only TEST_PHONE receives messages; phone {phone[-4:]} suppressed"
        return True, "test dispatch to TEST_PHONE"
    if DISPATCH_MODE == "live":
        return True, "live dispatch"
    # Unknown mode → treat as off
    return False, f"unknown DISPATCH_MODE={DISPATCH_MODE!r} — defaulting to off"


def _normalize_phone(phone: str) -> str:
    """Strip formatting, ensure 91- country prefix for India."""
    p = "".join(c for c in (phone or "") if c.isdigit())
    if not p:
        return ""
    if p.startswith("0"):  # drop trunk prefix
        p = p[1:]
    if not p.startswith("91") and len(p) == 10:
        p = "91" + p
    return p


# ============================================================================
# WhatsApp via MSG91
# ============================================================================


async def send_whatsapp(
    phone: str,
    message: str,
    *,
    template_id: Optional[str] = None,
    store_id: Optional[str] = None,
    variables: Optional[dict] = None,
) -> DispatchResult:
    """
    Send a WhatsApp template message via MSG91. Returns DispatchResult;
    never raises.

    template_id: the FLOW KEY. The actual Meta/MSG91 template name, language,
      category and variable order come from the template registry (ONE lookup:
      api.services.notification_templates.resolve_wa_template -- owner-edited
      DB row over the code seed). A flow with NO mapping REFUSES honestly:
      a guessed template name is never sent, in any DISPATCH_MODE.

    store_id: Coexistence sender context. The integrated number is resolved
      per store (ONE resolver: integration_config.resolve_whatsapp_sender);
      no/unmapped store falls back to the single default number.

    variables: optional values for the registry's ordered variable list. When
      every listed variable is supplied, the payload carries body_1..body_n in
      registry order; otherwise the whole message rides body_1 (legacy shape).

    Template + sender are resolved BEFORE the dispatch gate so the SIMULATED
    path proves the payload shape end to end (see DispatchResult.meta).
    """
    phone_norm = _normalize_phone(phone)
    if not phone_norm:
        return DispatchResult(ok=False, status="FAILED", error="invalid phone", channel="whatsapp")

    # --- template registry: the ONE lookup; unmapped flows refuse ----------
    from api.services.notification_templates import resolve_wa_template

    tpl = resolve_wa_template(template_id)
    if tpl is None:
        return DispatchResult(
            ok=False,
            status="FAILED",
            error=(
                f"no WhatsApp template mapped for flow '{template_id or '(none)'}' "
                "- map it under Settings -> Notifications -> Templates; a guessed "
                "template name is never sent"
            ),
            channel="whatsapp",
        )

    # --- sender resolution: per-store Coexistence number -------------------
    from api.services.integration_config import resolve_whatsapp_sender

    integrated_number = resolve_whatsapp_sender(store_id)

    # --- components: registry variable order when values are supplied ------
    var_order = tpl.get("variables") or []
    components = None
    if variables and var_order and all(v in variables for v in var_order):
        components = {
            f"body_{i}": {"type": "text", "value": str(variables[name])}
            for i, name in enumerate(var_order, start=1)
        }
    if components is None:
        components = {"body_1": {"type": "text", "value": message}}

    meta = {
        "flow_key": template_id,
        "template_name": tpl["template_name"],
        "language": tpl["language"],
        "category": tpl["category"],
        "store_id": store_id,
        "integrated_number": integrated_number,
        "components": components,
    }

    should, reason = _should_dispatch(phone_norm)
    if not should:
        logger.info(f"[PROVIDER] Suppressed WhatsApp to {phone_norm[-4:]}: {reason}")
        return DispatchResult(
            ok=True,
            status="SIMULATED",
            provider_id=f"sim-wa-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            channel="whatsapp",
            meta=meta,
        )

    creds = _msg91()
    api_key = creds.get("api_key") or ""

    if not api_key:
        return DispatchResult(
            ok=False,
            status="FAILED",
            error="MSG91 auth key not configured "
                  "(Settings -> Integrations -> WhatsApp Business, or MSG91_API_KEY)",
            channel="whatsapp",
        )

    if not integrated_number:
        return DispatchResult(
            ok=False,
            status="FAILED",
            error="MSG91 WhatsApp integrated number not configured "
                  "(Settings -> Integrations -> WhatsApp Business, or "
                  "MSG91_WHATSAPP_INTEGRATED_NUMBER)",
            channel="whatsapp",
        )

    # MSG91 WhatsApp API — "send-template-message" endpoint shape.
    # https://docs.msg91.com/whatsapp/send-message
    payload = {
        "integrated_number": integrated_number,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": tpl["template_name"],
                "language": {"code": tpl["language"], "policy": "deterministic"},
                "namespace": os.getenv("MSG91_WHATSAPP_NAMESPACE", ""),
                "to_and_components": [
                    {
                        "to": [phone_norm],
                        "components": components,
                    }
                ],
            },
        },
    }
    headers = {"authkey": api_key, "content-type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.post(
                f"{MSG91_BASE_URL}/whatsapp/whatsapp-outbound-message/bulk/",
                headers=headers,
                json=payload,
            )
        if resp.status_code not in (200, 201, 202):
            logger.warning(f"[PROVIDER] MSG91 WA {resp.status_code}: {resp.text[:300]}")
            return DispatchResult(
                ok=False,
                status="FAILED",
                error=f"MSG91 returned {resp.status_code}",
                channel="whatsapp",
            )
        body = resp.json()
        # MSG91 returns request_id on success
        request_id = (
            body.get("request_id")
            or (body.get("data") or {}).get("request_id")
            or ""
        )
        return DispatchResult(
            ok=True,
            status="SENT",
            provider_id=request_id or None,
            channel="whatsapp",
            meta=meta,
        )
    except httpx.TimeoutException:
        return DispatchResult(ok=False, status="FAILED", error="timeout", channel="whatsapp")
    except httpx.HTTPError as e:
        return DispatchResult(ok=False, status="FAILED", error=f"http {e}", channel="whatsapp")
    except (ValueError, KeyError, TypeError) as e:
        return DispatchResult(ok=False, status="FAILED", error=f"parse {e}", channel="whatsapp")


# ============================================================================
# SMS via MSG91 (fallback channel if WhatsApp undeliverable / DND)
# ============================================================================


async def send_sms(phone: str, message: str) -> DispatchResult:
    """Send a transactional SMS via MSG91. Returns DispatchResult; never raises."""
    phone_norm = _normalize_phone(phone)
    if not phone_norm:
        return DispatchResult(ok=False, status="FAILED", error="invalid phone", channel="sms")

    should, reason = _should_dispatch(phone_norm)
    if not should:
        logger.info(f"[PROVIDER] Suppressed SMS to {phone_norm[-4:]}: {reason}")
        return DispatchResult(
            ok=True,
            status="SIMULATED",
            provider_id=f"sim-sms-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            channel="sms",
        )

    creds = _msg91()
    api_key = creds.get("api_key") or ""
    if not api_key:
        return DispatchResult(
            ok=False,
            status="FAILED",
            error="MSG91 auth key not configured "
                  "(Settings -> Integrations -> WhatsApp Business, or MSG91_API_KEY)",
            channel="sms",
        )

    # MSG91 SMS Flow API. Requires DLT-approved template + sender ID.
    # https://docs.msg91.com/sms/send-sms
    payload = {
        "template_id": creds.get("sms_template_id") or "",
        "sender": creds.get("sender") or "",
        "short_url": "0",
        "recipients": [{"mobiles": phone_norm, "BODY": message}],
    }
    headers = {"authkey": api_key, "content-type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
            resp = await client.post(f"{MSG91_BASE_URL}/flow/", headers=headers, json=payload)
        if resp.status_code not in (200, 201, 202):
            logger.warning(f"[PROVIDER] MSG91 SMS {resp.status_code}: {resp.text[:300]}")
            return DispatchResult(
                ok=False,
                status="FAILED",
                error=f"MSG91 returned {resp.status_code}",
                channel="sms",
            )
        body = resp.json()
        return DispatchResult(
            ok=True,
            status="SENT",
            provider_id=body.get("request_id") or None,
            channel="sms",
        )
    except httpx.TimeoutException:
        return DispatchResult(ok=False, status="FAILED", error="timeout", channel="sms")
    except httpx.HTTPError as e:
        return DispatchResult(ok=False, status="FAILED", error=f"http {e}", channel="sms")
    except (ValueError, KeyError, TypeError) as e:
        return DispatchResult(ok=False, status="FAILED", error=f"parse {e}", channel="sms")


# ============================================================================
# Short URLs via MSG91 (click tracking on review-request / recall links)
# ============================================================================

# Endpoint path under MSG91_BASE_URL. Env-overridable so the exact path can be
# corrected at arming time without a code change (MSG91's short-URL API is
# panel-documented; nothing here is ever called while dark).
MSG91_SHORTURL_PATH = (os.getenv("MSG91_SHORTURL_PATH") or "shorturl").strip("/")


async def shorten_url(url: str) -> str:
    """Wrap ONE http(s) link in an MSG91 short URL so the click comes back on
    the /integrations/msg91/webhooks/shorturl receiver as a message_events
    row with event=clicked. Returns the URL to put in the message; NEVER
    raises.

    DARK-SAFE BY CONSTRUCTION: with DISPATCH_MODE=off (the default) or no
    MSG91 auth key, the input is returned UNTOUCHED -- message bodies stay
    byte-identical to a deploy without this feature. Any provider failure
    (non-200, timeout, unrecognised response) also passes the original
    through: a long link that works beats a short link that does not.
    """
    original = str(url or "")
    if not original.startswith(("http://", "https://")):
        return original
    if DISPATCH_MODE not in ("test", "live"):
        return original

    creds = _msg91()
    api_key = creds.get("api_key") or ""
    if not api_key:
        return original

    try:
        async with httpx.AsyncClient(timeout=min(PROVIDER_TIMEOUT, 5.0)) as client:
            resp = await client.post(
                f"{MSG91_BASE_URL}/{MSG91_SHORTURL_PATH}/",
                headers={"authkey": api_key, "content-type": "application/json"},
                json={"url": original},
            )
        if resp.status_code not in (200, 201):
            logger.warning(
                f"[PROVIDER] MSG91 shorturl {resp.status_code}: {resp.text[:200]}"
            )
            return original
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        candidates = [
            (data or {}).get("shortUrl") if isinstance(data, dict) else None,
            (data or {}).get("short_url") if isinstance(data, dict) else None,
            data if isinstance(data, str) else None,
            body.get("shortUrl") if isinstance(body, dict) else None,
            body.get("short_url") if isinstance(body, dict) else None,
        ]
        for cand in candidates:
            short = str(cand or "").strip()
            if short.startswith(("http://", "https://")):
                return short
        logger.warning("[PROVIDER] MSG91 shorturl response had no short link")
        return original
    except httpx.HTTPError as e:
        logger.warning(f"[PROVIDER] MSG91 shorturl failed: {e}")
        return original
    except (ValueError, KeyError, TypeError) as e:
        logger.warning(f"[PROVIDER] MSG91 shorturl parse failed: {e}")
        return original


# ============================================================================
# Capability probe
# ============================================================================


def dispatch_mode() -> str:
    return DISPATCH_MODE


def provider_ready(channel: str) -> bool:
    """True if we have enough config to actually hit the provider.

    Resolved fresh (screen first, then env) so this answers "would a send
    work RIGHT NOW", not "was an env var set when the process booted".
    """
    creds = _msg91()
    if not creds.get("api_key"):
        return False
    if channel == "whatsapp":
        return bool(creds.get("whatsapp_number"))
    if channel == "sms":
        return bool(creds.get("sms_template_id"))
    return False
