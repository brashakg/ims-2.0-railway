"""
IMS 2.0 — Outbound notification providers (WhatsApp + SMS)
=============================================================
Thin async clients for the Indian messaging providers IMS 2.0 uses:

- MSG91 (default): WhatsApp Business + Transactional SMS + OTP. Single vendor,
  single API key, DLT-compliant for Indian telecom regulations.
- Twilio (optional): international SMS fallback. Not wired in Phase 4.2.

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

DISPATCH_MODE = os.getenv("DISPATCH_MODE", "off").lower()  # off | test | live
TEST_PHONE = os.getenv("TEST_PHONE", "")

# MSG91 config
MSG91_API_KEY = os.getenv("MSG91_API_KEY", "")
MSG91_SENDER = os.getenv("MSG91_SENDER", "BVOPTL")  # DLT-registered sender ID
MSG91_WHATSAPP_INTEGRATED_NUMBER = os.getenv("MSG91_WHATSAPP_INTEGRATED_NUMBER", "")
MSG91_SMS_TEMPLATE_ID = os.getenv("MSG91_SMS_TEMPLATE_ID", "")
MSG91_BASE_URL = "https://control.msg91.com/api/v5"

PROVIDER_TIMEOUT = float(os.getenv("PROVIDER_TIMEOUT", "15.0"))


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

    if not MSG91_API_KEY:
        return DispatchResult(
            ok=False,
            status="FAILED",
            error="MSG91_API_KEY unset",
            channel="whatsapp",
        )

    if not MSG91_WHATSAPP_INTEGRATED_NUMBER:
        return DispatchResult(
            ok=False,
            status="FAILED",
            error="MSG91_WHATSAPP_INTEGRATED_NUMBER unset",
            channel="whatsapp",
        )

    # MSG91 WhatsApp API — "send-template-message" endpoint shape.
    # https://docs.msg91.com/whatsapp/send-message
    payload = {
        "integrated_number": MSG91_WHATSAPP_INTEGRATED_NUMBER,
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
    headers = {"authkey": MSG91_API_KEY, "content-type": "application/json"}

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

    if not MSG91_API_KEY:
        return DispatchResult(ok=False, status="FAILED", error="MSG91_API_KEY unset", channel="sms")

    # MSG91 SMS Flow API. Requires DLT-approved template + sender ID.
    # https://docs.msg91.com/sms/send-sms
    payload = {
        "template_id": MSG91_SMS_TEMPLATE_ID,
        "sender": MSG91_SENDER,
        "short_url": "0",
        "recipients": [{"mobiles": phone_norm, "BODY": message}],
    }
    headers = {"authkey": MSG91_API_KEY, "content-type": "application/json"}

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
    """True if we have enough env config to actually hit the provider."""
    if not MSG91_API_KEY:
        return False
    if channel == "whatsapp":
        return bool(MSG91_WHATSAPP_INTEGRATED_NUMBER)
    if channel == "sms":
        return bool(MSG91_SMS_TEMPLATE_ID)
    return False
