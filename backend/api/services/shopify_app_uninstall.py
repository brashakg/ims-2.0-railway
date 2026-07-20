"""
IMS 2.0 - Shopify APP UNINSTALLED -> loud alert + integration-health record
============================================================================
Handles the Shopify `app/uninstalled` webhook. This is the single most important
integration-health signal there is: once the app is uninstalled Shopify REVOKES
the access token, so every subsequent push / pull / webhook silently dies. IMS
must scream about it, not let it rot.

WHAT THIS DOES (all fail-soft, all idempotent):
  1. LOGS LOUDLY (logger.error) so it is unmissable in the container logs.
  2. Writes an integration-health/alert row to `integration_alerts`
     (type=shopify, kind=app_uninstalled, resolved=False) so the SUPERADMIN
     integrations/status surface can show the connection is gone. Idempotent: at
     most one UNRESOLVED app_uninstalled row per shop.
  3. Stamps the `integrations.shopify` config doc with app_uninstalled_at +
     connected=False (the health marker). Idempotent $set; leaves `enabled`
     untouched (owner decides whether to disable the tile).
  4. Raises a HIGH audit alert via the existing audit_alerts.emit_audit_alert
     (writes audit_logs + dispatches audit.alert so SENTINEL fans it out). The
     alert fires ONCE -- only on the first (newly-recorded) uninstall.

IDEMPOTENT: keyed on the presence of an UNRESOLVED integration_alerts row for the
shop. A re-delivered app/uninstalled finds that row and returns "duplicate"
without re-alerting.

PUBLIC API (async -- it awaits the async emit_audit_alert):
    await handle_shopify_app_uninstalled(db, payload, *, topic=None) -> dict
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ALERTS_COLLECTION = "integration_alerts"
_ALERT_KIND = "app_uninstalled"


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _resolve_shop(payload: Dict[str, Any]) -> str:
    """The uninstalled shop's identity. The app/uninstalled payload is the SHOP
    object -- prefer the stable myshopify domain, then the primary domain, then
    the numeric shop id."""
    return (
        _norm(payload.get("myshopify_domain"))
        or _norm(payload.get("domain"))
        or _norm(payload.get("id"))
        or "unknown-shop"
    )


def _existing_unresolved_alert(db, shop: str) -> Optional[Dict[str, Any]]:
    """An open (unresolved) app_uninstalled alert already recorded for this shop,
    which makes a redelivery a no-op. Fail-soft -> None."""
    if db is None:
        return None
    try:
        coll = db.get_collection(_ALERTS_COLLECTION)
        if coll is None:
            return None
        return coll.find_one(
            {
                "type": "shopify",
                "kind": _ALERT_KIND,
                "shop": shop,
                "resolved": {"$ne": True},
            }
        )
    except Exception:  # noqa: BLE001
        logger.debug("[SHOPIFY_APP_UNINSTALL] alert lookup failed", exc_info=True)
        return None


def _record_health(db, shop: str, payload: Dict[str, Any], now: str) -> None:
    """Write the integration_alerts row + stamp the integrations.shopify doc.
    Fail-soft: a write error is logged, never raised."""
    try:
        coll = db.get_collection(_ALERTS_COLLECTION)
        if coll is not None:
            coll.insert_one(
                {
                    "type": "shopify",
                    "kind": _ALERT_KIND,
                    "severity": "HIGH",
                    "shop": shop,
                    "message": (
                        f"Shopify app was uninstalled from {shop}. The access "
                        f"token is revoked -- all product / order / inventory "
                        f"sync and webhooks are DOWN until the app is reinstalled."
                    ),
                    "resolved": False,
                    "created_at": now,
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_APP_UNINSTALL] alert-row write failed: %s", exc)

    # Stamp the shopify integration config (health marker) if a doc exists. We do
    # NOT flip `enabled` -- that is an owner decision -- only record the fact.
    try:
        integ = db.get_collection("integrations")
        if integ is not None:
            integ.update_one(
                {"type": "shopify"},
                {
                    "$set": {
                        "connected": False,
                        "app_uninstalled_at": now,
                        "app_uninstalled_shop": shop,
                    }
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_APP_UNINSTALL] integrations stamp failed: %s", exc)


async def _raise_high_alert(shop: str, payload: Dict[str, Any]) -> Optional[str]:
    """Raise a HIGH audit alert through the existing alert spine
    (audit_alerts.emit_audit_alert -> audit_logs + audit.alert event -> SENTINEL).
    Fail-soft: emit_audit_alert never raises, and we swallow an import error."""
    try:
        from .audit_alerts import emit_audit_alert

        return await emit_audit_alert(
            severity="HIGH",
            action="integration.shopify.uninstalled",
            entity_type="integration",
            entity_id="shopify",
            user_id="SYSTEM_SHOPIFY_WEBHOOK",
            context={
                "shop": shop,
                "reason": "Shopify app/uninstalled webhook",
                "impact": "access token revoked; all Shopify sync + webhooks down",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_APP_UNINSTALL] HIGH alert dispatch failed: %s", exc)
        return None


async def handle_shopify_app_uninstalled(
    db, payload: Dict[str, Any], *, topic: Optional[str] = None
) -> Dict[str, Any]:
    """Record + alert on a Shopify app uninstall. Idempotent (the HIGH alert fires
    only on the first record). NEVER raises.

    Returns a structured result dict:
      {"status": "recorded"|"duplicate"|"simulated", "shop": <str>,
       "alerted": bool, "audit_id": <str|None>}
    """
    try:
        payload = payload if isinstance(payload, dict) else {}
        shop = _resolve_shop(payload)

        # LOUD, unmissable log regardless of DB state.
        logger.error(
            "[SHOPIFY_APP_UNINSTALL] Shopify app UNINSTALLED for shop=%s -- the "
            "access token is revoked; all Shopify sync + webhooks are now DOWN.",
            shop,
        )

        if db is None:
            return {"status": "simulated", "shop": shop, "alerted": False}

        # IDEMPOTENCY: a redelivery must not double-record or re-alert.
        if _existing_unresolved_alert(db, shop) is not None:
            logger.info(
                "[SHOPIFY_APP_UNINSTALL] shop=%s already has an open uninstall "
                "alert -- duplicate, no re-alert",
                shop,
            )
            return {"status": "duplicate", "shop": shop, "alerted": False}

        now = datetime.now(timezone.utc).isoformat()
        _record_health(db, shop, payload, now)

        # Raise the HIGH alert ONCE, on the first record.
        audit_id = await _raise_high_alert(shop, payload)

        return {
            "status": "recorded",
            "shop": shop,
            "alerted": True,
            "audit_id": audit_id,
        }
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning(
            "[SHOPIFY_APP_UNINSTALL] handle_shopify_app_uninstalled failed soft: %s",
            exc,
        )
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
