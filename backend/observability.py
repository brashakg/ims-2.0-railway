"""
IMS 2.0 - Observability helpers
=================================
Cross-cutting observability primitives used by agents, API routes, and the
Jarvis event bus. Every helper is fail-soft: if the relevant env var is
missing, the function is a no-op (Sentry breadcrumb skipped, Slack post
skipped). A fresh Railway deploy without observability configured still
boots green - no ImportError, no 500s.

Exports:
  - agent_tick_span(agent_id, tick_kind)   async context manager wrapping an
                                           agent background tick in a Sentry
                                           transaction with tags; returns the
                                           transaction or None
  - capture_agent_error(agent_id, exc, ...) sets Sentry scope + captures
                                            exception so it surfaces tagged
                                            by agent_id
  - notify_slack(severity, title, body, metadata)
                                            POSTs a formatted Block Kit
                                            message to SLACK_WEBHOOK_URL for
                                            qualifying severities
  - is_slack_configured()                   True if SLACK_WEBHOOK_URL set

Env vars (all optional):
  - SENTRY_DSN              APM + error tracking. If unset, Sentry is no-op.
  - SLACK_WEBHOOK_URL       Incoming webhook URL for anomaly alerts.
  - SLACK_ALERT_SEVERITY    Minimum severity to notify (default CRITICAL).
                            Options: CRITICAL | HIGH | MEDIUM | LOW
  - SLACK_TIMEOUT           Seconds before a slack POST gives up (default 10).

No ASCII-tag prints in this module - everything goes through `logger`.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import logging
import os

import httpx

logger = logging.getLogger(__name__)


# ============================================================================
# Sentry helpers - all no-op when sentry_sdk is absent or DSN unset
# ============================================================================


def _sentry_sdk():
    """Return sentry_sdk module if initialized with a DSN, else None."""
    if not os.getenv("SENTRY_DSN"):
        return None
    try:
        import sentry_sdk
        # Hub.current.client is None when sentry_sdk.init was never called or
        # failed. Treat that as a no-op so we don't emit into the void.
        if sentry_sdk.Hub.current.client is None:
            return None
        return sentry_sdk
    except Exception:
        return None


@asynccontextmanager
async def agent_tick_span(agent_id: str, tick_kind: str = "background"):
    """
    Wrap an agent background tick in a Sentry transaction so each run shows up
    as its own distributed trace in the Sentry performance view, tagged with
    agent_id for easy filtering.

    Usage:
        async with agent_tick_span("oracle", "background") as txn:
            await do_work()

    Yields the sentry_sdk transaction handle or None. Failing-soft: if Sentry
    is unavailable or the transaction creation raises, we still yield None and
    the caller's work proceeds normally.
    """
    sentry = _sentry_sdk()
    transaction = None
    if sentry is not None:
        try:
            transaction = sentry.start_transaction(
                op="agent.tick",
                name=f"{agent_id}.{tick_kind}",
            )
            scope = sentry.Hub.current.scope
            if scope is not None:
                scope.set_tag("agent.id", agent_id)
                scope.set_tag("agent.kind", tick_kind)
        except Exception as e:
            logger.debug(f"[OBSERVABILITY] sentry start_transaction failed: {e}")
            transaction = None

    try:
        yield transaction
    except Exception as exc:
        # Let Sentry capture it with the agent scope still set, then re-raise
        # so the caller's own error handling still runs (background_tick logs +
        # updates run stats). We do NOT swallow.
        if sentry is not None:
            try:
                sentry.capture_exception(exc)
            except Exception:
                pass
        raise
    finally:
        if transaction is not None:
            try:
                transaction.finish()
            except Exception:
                pass


def capture_agent_error(agent_id: str, exc: BaseException,
                         extra: Optional[Dict[str, Any]] = None) -> None:
    """
    Annotate current Sentry scope with agent context and capture the exception.
    Safe to call in a failure branch even when Sentry is not configured.
    """
    sentry = _sentry_sdk()
    if sentry is None:
        return
    try:
        with sentry.push_scope() as scope:
            scope.set_tag("agent.id", agent_id)
            if extra:
                for k, v in extra.items():
                    try:
                        scope.set_extra(k, v)
                    except Exception:
                        pass
            sentry.capture_exception(exc)
    except Exception as e:
        logger.debug(f"[OBSERVABILITY] capture_agent_error failed: {e}")


# ============================================================================
# Slack webhook alerts - used by ORACLE for CRITICAL / HIGH anomalies
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
