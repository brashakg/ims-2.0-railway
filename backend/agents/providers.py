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


async def send_whatsapp(phone: str, message: str, *, template_id: Optional[str] = None) -> DispatchResult:
    """
    Send a WhatsApp message via MSG91. Returns DispatchResult; never raises.

    template_id: MSG91 DLT-approved template id. If None, MSG91 will reject
      the send (WhatsApp Business requires pre-approved templates). The
      template bindings themselves live in notification_service.TEMPLATES
      and are passed as the message body here.
    """
    phone_norm = _normalize_phone(phone)
    if not phone_norm:
        return DispatchResult(ok=False, status="FAILED", error="invalid phone", channel="whatsapp")

    should, reason = _should_dispatch(phone_norm)
    if not should:
        logger.info(f"[PROVIDER] Suppressed WhatsApp to {phone_norm[-4:]}: {reason}")
        return DispatchResult(
            ok=True,
            status="SIMULATED",
            provider_id=f"sim-wa-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            channel="whatsapp",
        )

    creds = _msg91()
    api_key = creds.get("api_key") or ""
    integrated_number = creds.get("whatsapp_number") or ""

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
                "name": template_id or "generic_text",
                "language": {"code": "en", "policy": "deterministic"},
                "namespace": os.getenv("MSG91_WHATSAPP_NAMESPACE", ""),
                "to_and_components": [
                    {
                        "to": [phone_norm],
                        "components": {
                            "body_1": {"type": "text", "value": message},
                        },
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
