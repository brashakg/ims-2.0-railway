"""
IMS 2.0 - Observability helpers
=================================
Slack alerting primitives used by agents (SENTINEL) and the Jarvis event bus.
Every helper is fail-soft: if SLACK_WEBHOOK_URL is missing, the function is a
no-op. A fresh Railway deploy without observability configured still boots
green - no ImportError, no 500s.

Error/health monitoring is handled IN-HOUSE by the SENTINEL agent: it persists
health snapshots to `health_checks`, alerts to `alert_history`, and consumes
`agent.error` events emitted by the base agent on a failed tick. There is no
external APM dependency (no sentry.io).

Exports:
  - notify_slack(severity, title, body, metadata)
                                            POSTs a formatted Block Kit
                                            message to SLACK_WEBHOOK_URL for
                                            qualifying severities
  - is_slack_configured()                   True if SLACK_WEBHOOK_URL set

Env vars (all optional):
  - SLACK_WEBHOOK_URL       Incoming webhook URL for anomaly alerts.
  - SLACK_ALERT_SEVERITY    Minimum severity to notify (default CRITICAL).
                            Options: CRITICAL | HIGH | MEDIUM | LOW
  - SLACK_TIMEOUT           Seconds before a slack POST gives up (default 10).

No ASCII-tag prints in this module - everything goes through `logger`.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import logging
import os

import httpx

logger = logging.getLogger(__name__)


# ============================================================================
# Slack webhook alerts - used by SENTINEL / ORACLE for CRITICAL / HIGH anomalies
# ============================================================================


_SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
_SEVERITY_COLOR = {
    "CRITICAL": "#dc2626",  # red-600
    "HIGH":     "#ea580c",  # orange-600
    "MEDIUM":   "#eab308",  # yellow-500
    "LOW":      "#3b82f6",  # blue-500
}


def is_slack_configured() -> bool:
    """True if SLACK_WEBHOOK_URL is set."""
    return bool(os.getenv("SLACK_WEBHOOK_URL", "").strip())


def _should_alert(severity: str) -> bool:
    """Compare given severity to SLACK_ALERT_SEVERITY threshold."""
    threshold = os.getenv("SLACK_ALERT_SEVERITY", "CRITICAL").upper()
    sev_rank = _SEVERITY_RANK.get(severity.upper(), 0)
    thr_rank = _SEVERITY_RANK.get(threshold, 4)
    return sev_rank >= thr_rank


def _build_slack_payload(severity: str, title: str, body: str,
                         metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Assemble a Slack attachment. Extracted for unit tests."""
    color = _SEVERITY_COLOR.get(severity.upper(), "#64748b")
    fields = []
    if metadata:
        for k, v in metadata.items():
            # Slack's short:true works for values <= ~25 chars - keep it simple
            fields.append({
                "title": str(k),
                "value": str(v) if v is not None else "-",
                "short": len(str(v)) <= 25 if v is not None else True,
            })
    return {
        "attachments": [{
            "color": color,
            "title": f"[{severity.upper()}] {title}",
            "text": body or "",
            "fields": fields,
            "footer": "IMS 2.0 - Jarvis",
            "ts": int(datetime.now(timezone.utc).timestamp()),
        }]
    }


async def notify_slack(severity: str, title: str, body: str,
                       metadata: Optional[Dict[str, Any]] = None) -> bool:
    """
    POST a formatted alert to SLACK_WEBHOOK_URL.

    Returns True if the message was delivered (HTTP 2xx from Slack), False
    otherwise - including when Slack is not configured or severity is below
    the configured threshold. Never raises.
    """
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return False
    if not _should_alert(severity):
        logger.debug(f"[OBSERVABILITY] slack: {severity} below threshold; skipping")
        return False

    payload = _build_slack_payload(severity, title, body, metadata)
    timeout = httpx.Timeout(float(os.getenv("SLACK_TIMEOUT", "10.0")))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(webhook, json=payload)
            if 200 <= resp.status_code < 300:
                logger.info(f"[OBSERVABILITY] slack notified: {title}")
                return True
            logger.warning(
                f"[OBSERVABILITY] slack POST non-2xx: {resp.status_code} "
                f"body={resp.text[:200]}"
            )
            return False
    except Exception as e:
        logger.warning(f"[OBSERVABILITY] slack POST failed: {e}")
        return False
