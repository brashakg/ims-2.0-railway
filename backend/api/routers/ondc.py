"""
IMS 2.0 - ONDC Seller Node Router  (Backlog BVI-20)
====================================================
Protocol callback endpoints that the SNP (Seller Network Participant) calls
on IMS, plus a SUPERADMIN/ADMIN status + admin surface.

Mounted at /api/v1/ondc

CALLBACK SURFACE (PUBLIC-ish -- no IMS JWT required; protected by SNP
signature verification when IMS_ONDC_UKP / the integration config has
a ukp key):

  POST /on_search     SNP triggers catalog push (we publish to the network)
  POST /on_select     Buyer has selected items; confirm availability
  POST /on_init       Buyer is ready to proceed; confirm order details
  POST /on_confirm    Order confirmed; ingest into IMS orders
  POST /on_status     Buyer queries order status
  POST /on_cancel     Buyer or SNP cancels the order

ADMIN SURFACE (SUPERADMIN / ADMIN JWT required):

  GET  /status        enabled? last publish? ONDC order count + TCS total
  POST /publish       Manually trigger catalog publish to SNP

DARK CONTRACT: callbacks are fail-soft -- a bad / unsigned payload logs a
warning and returns the Beckn ACK (required by protocol) without crashing.
Order ingestion is gated: when IMS_ONDC_ENABLED is off, on_confirm logs the
payload but does NOT write to `orders`.

SIGNATURE VERIFICATION: when the `integrations.ondc.config.ukp` key is set,
every inbound callback validates the `Authorization` header HMAC-SHA256
signature. Without a ukp the gate is skipped (useful for dev / SNP sandbox
where signatures are optional).

AUDIT: every on_confirm + on_cancel attempt writes an audit_logs row
(fail-soft -- audit failure never blocks the Beckn ACK).

RBAC catalogued in api/services/rbac_policy.POLICY. Callback routes are
PUBLIC (Beckn protocol requires no IMS auth). Admin routes require
SUPERADMIN or ADMIN.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .auth import require_roles
from ..services import ondc_seller

router = APIRouter()
logger = logging.getLogger(__name__)

# Admin routes: SUPERADMIN auto-granted; explicitly list ADMIN.
_ADMIN_ROLES = ("ADMIN",)


# ---------------------------------------------------------------------------
# DB helpers (fail-soft; mirror online_store_push.py)
# ---------------------------------------------------------------------------


def _get_db():
    """Return the underlying DB object or None (never raises)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# Beckn ACK / NACK helpers
# ---------------------------------------------------------------------------

_ACK = {"message": {"ack": {"status": "ACK"}}}
_NACK_INVALID_SIG = {
    "message": {"ack": {"status": "NACK"}},
    "error": {"type": "DOMAIN-ERROR", "code": "10001", "message": "Invalid signature"},
}


def _beckn_ack(context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Wrap the Beckn ACK envelope with the echoed context."""
    resp: Dict[str, Any] = {
        "context": context or {},
        **_ACK,
    }
    return resp


def _beckn_nack(
    context: Optional[Dict[str, Any]] = None,
    code: str = "10001",
    message: str = "Invalid request",
) -> Dict[str, Any]:
    return {
        "context": context or {},
        "message": {"ack": {"status": "NACK"}},
        "error": {"type": "DOMAIN-ERROR", "code": code, "message": message},
    }


# ---------------------------------------------------------------------------
# Signature verification helper
# ---------------------------------------------------------------------------


def _load_ukp(db) -> Optional[str]:
    """Load the UKP (HMAC signing secret) from the integrations collection."""
    try:
        from ..services.ondc_seller import _load_ondc_config

        cfg = _load_ondc_config(db)
        ukp = cfg.get("ukp", "")
        return ukp if ukp else None
    except Exception:
        return None


def _verify_signature(auth_header: str, body: bytes, ukp: str) -> bool:
    """Verify inbound HMAC-SHA256 signature from the SNP.

    Header format (set by our own _sign_request; SNPs may use a different
    format -- adapt if the SNP uses Ed25519). Returns True when valid,
    False on mismatch / missing.
    """
    try:
        # Extract signature value from the header
        parts = dict(p.split("=", 1) for p in auth_header.split(",") if "=" in p)
        sig_value = parts.get("signature", "")
        expected = hmac.new(ukp.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig_value, expected)
    except Exception as exc:
        logger.debug("[ONDC] Signature parse error: %s", exc)
        return False


async def _verify_callback(request: Request, db) -> Optional[Dict[str, Any]]:
    """Parse + optionally verify an incoming Beckn callback.

    Returns the parsed JSON payload dict on success, None on hard failure
    (unparse-able body). Signature failure returns the payload too but logs
    a warning (we ACK either way to maintain protocol compliance -- real
    rejection happens at the SNP level; we just won't process the order).
    """
    try:
        body = await request.body()
        payload = json.loads(body)
    except Exception as exc:
        logger.warning("[ONDC] Failed to parse callback body: %s", exc)
        return None

    ukp = _load_ukp(db)
    if ukp:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header or not _verify_signature(auth_header, body, ukp):
            logger.warning(
                "[ONDC] Signature verification failed for %s",
                request.url.path,
            )
            # Log and continue -- protocol says always ACK; mark as unverified
            payload["_signature_invalid"] = True
        else:
            payload["_signature_invalid"] = False
    else:
        payload["_signature_invalid"] = False  # No UKP configured -> skip check

    return payload


def _write_audit(db, action: str, payload: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Write an audit_logs row. Fail-soft -- never raises."""
    if db is None:
        return
    try:
        db.get_collection("audit_logs").insert_one({
            "action": f"ONDC_{action.upper()}",
            "entity": "ondc_order",
            "entity_id": (payload.get("message") or {}).get("order", {}).get("id", "?"),
            "performed_by": "SNP_CALLBACK",
            "result": result.get("ok"),
            "details": {
                "mode": result.get("mode"),
                "order_id": result.get("order_id"),
                "error": result.get("error"),
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        logger.debug("[ONDC] Audit write failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Protocol callback endpoints (PUBLIC -- no IMS JWT)
# ---------------------------------------------------------------------------


@router.post("/on_search")
async def on_search(request: Request) -> Dict[str, Any]:
    """SNP triggers a /search: IMS responds by publishing the catalog.

    Fail-soft: always returns ACK. Catalog push is DARK when not enabled.
    """
    db = _get_db()
    payload = await _verify_callback(request, db)
    if payload is None:
        return _beckn_nack(message="Malformed request body")

    context = payload.get("context") or {}

    # Trigger catalog publish asynchronously (best-effort)
    try:
        result = await ondc_seller.publish_catalog(db)
        logger.info(
            "[ONDC] on_search -> publish_catalog mode=%s items=%d",
            result.get("mode"),
            result.get("item_count", 0),
        )
    except Exception as exc:
        logger.error("[ONDC] on_search publish error: %s", exc)

    return _beckn_ack(context)


@router.post("/on_select")
async def on_select(request: Request) -> Dict[str, Any]:
    """Buyer has selected items; confirm availability / quote.

    Stub: always ACKs with the items available in IMS stock.
    Full implementation requires the SNP's /select flow integration.
    """
    db = _get_db()
    payload = await _verify_callback(request, db)
    if payload is None:
        return _beckn_nack(message="Malformed request body")

    context = payload.get("context") or {}
    logger.info(
        "[ONDC] on_select received (transaction=%s)", context.get("transaction_id", "?")
    )
    return _beckn_ack(context)


@router.post("/on_init")
async def on_init(request: Request) -> Dict[str, Any]:
    """Buyer is ready to proceed; confirm order details + quote final price.

    Stub: ACKs and prepares the order draft. Full quote / breakup logic
    to be added in BVI-20 follow-up.
    """
    db = _get_db()
    payload = await _verify_callback(request, db)
    if payload is None:
        return _beckn_nack(message="Malformed request body")

    context = payload.get("context") or {}
    logger.info(
        "[ONDC] on_init received (transaction=%s)", context.get("transaction_id", "?")
    )
    return _beckn_ack(context)


@router.post("/on_confirm")
async def on_confirm(request: Request) -> Dict[str, Any]:
    """Order confirmed by buyer -- ingest into IMS orders collection.

    This is the critical path: calls ondc_seller.ingest_ondc_order() which
    maps the Beckn order to a canonical IMS order (channel=ONDC).
    Writes an audit row. Fail-soft -- always ACKs even if ingestion fails
    (so the buyer doesn't hang; retry is the SNP's responsibility).
    """
    db = _get_db()
    payload = await _verify_callback(request, db)
    if payload is None:
        return _beckn_nack(message="Malformed request body")

    context = payload.get("context") or {}

    if payload.get("_signature_invalid"):
        logger.warning(
            "[ONDC] on_confirm: invalid signature -- skipping ingestion"
        )
        return _beckn_ack(context)

    result = ondc_seller.ingest_ondc_order(db, payload)
    _write_audit(db, "on_confirm", payload, result)

    if not result["ok"]:
        logger.error("[ONDC] on_confirm ingestion failed: %s", result.get("error"))
    else:
        logger.info(
            "[ONDC] on_confirm -> IMS order %s (mode=%s)",
            result.get("order_id"),
            result.get("mode"),
        )

    # Always ACK (protocol requirement)
    return _beckn_ack(context)


@router.post("/on_status")
async def on_status(request: Request) -> Dict[str, Any]:
    """Buyer queries order status. Return the IMS order status."""
    db = _get_db()
    payload = await _verify_callback(request, db)
    if payload is None:
        return _beckn_nack(message="Malformed request body")

    context = payload.get("context") or {}
    order_id = ((payload.get("message") or {}).get("order") or {}).get("id")

    status = "Unknown"
    if db is not None and order_id:
        try:
            doc = db.get_collection("orders").find_one(
                {"external_order_id": order_id, "channel": "ONDC"},
                {"_id": 0, "status": 1},
            )
            if doc:
                status = doc.get("status", "Unknown")
        except Exception as exc:
            logger.debug("[ONDC] on_status DB query failed: %s", exc)

    logger.info(
        "[ONDC] on_status order=%s -> %s", order_id or "?", status
    )
    return {
        **_beckn_ack(context),
        "message": {
            "order": {
                "id": order_id or "",
                "state": status,
            }
        },
    }


@router.post("/on_cancel")
async def on_cancel(request: Request) -> Dict[str, Any]:
    """Buyer or SNP cancels the order. Update IMS order status to CANCELLED."""
    db = _get_db()
    payload = await _verify_callback(request, db)
    if payload is None:
        return _beckn_nack(message="Malformed request body")

    context = payload.get("context") or {}
    order_data = (payload.get("message") or {}).get("order") or {}
    ondc_order_id = _s(order_data.get("id", ""))

    result: Dict[str, Any] = {"ok": False, "order_id": None, "mode": "SIMULATED", "error": "No DB"}
    if db is not None and ondc_order_id:
        try:
            res = db.get_collection("orders").find_one_and_update(
                {"external_order_id": ondc_order_id, "channel": "ONDC"},
                {
                    "$set": {
                        "status": "CANCELLED",
                        "cancelled_at": datetime.now(timezone.utc).isoformat(),
                        "cancellation_reason": _s(order_data.get("cancellation", {}).get("reason", {}).get("descriptor", {}).get("code", "BUYER_CANCELLED")),
                    }
                },
                return_document=True,
                projection={"_id": 0, "order_id": 1},
            )
            result = {
                "ok": res is not None,
                "order_id": (res or {}).get("order_id"),
                "mode": "LIVE",
                "error": None if res else "Order not found",
            }
        except Exception as exc:
            result = {"ok": False, "order_id": None, "mode": "LIVE", "error": str(exc)}
            logger.error("[ONDC] on_cancel DB update failed: %s", exc)

    _write_audit(db, "on_cancel", payload, result)
    logger.info(
        "[ONDC] on_cancel order=%s -> ok=%s", ondc_order_id or "?", result["ok"]
    )
    return _beckn_ack(context)


def _s(v: Any, default: str = "") -> str:
    return str(v or default).strip()


# ---------------------------------------------------------------------------
# ADMIN SURFACE (SUPERADMIN / ADMIN)
# ---------------------------------------------------------------------------


@router.get("/status")
async def ondc_status(
    current_user: dict = Depends(require_roles(*_ADMIN_ROLES)),
) -> Dict[str, Any]:
    """ONDC module status. Returns:
    - enabled (bool)
    - last_published_at (ISO or null)
    - last_item_count (int)
    - ondc_order_count (int)
    - tcs_total (float) -- sum of all recorded TCS amounts
    - simulated_reason (str or null)

    Fail-soft: missing DB -> counts are 0. Never 500s.
    """
    db = _get_db()
    enabled = ondc_seller.ondc_enabled(db)
    reason = None if enabled else ondc_seller._simulated_reason(db)

    last_published_at = None
    last_item_count = 0
    if db is not None:
        try:
            integ = db.get_collection("integrations").find_one(
                {"type": "ondc"},
                {"_id": 0, "last_published_at": 1, "last_item_count": 1},
            )
            if integ:
                last_published_at = integ.get("last_published_at")
                last_item_count = integ.get("last_item_count", 0)
        except Exception:
            pass

    ondc_order_count = 0
    if db is not None:
        try:
            ondc_order_count = db.get_collection("orders").count_documents(
                {"channel": "ONDC"}
            )
        except Exception:
            pass

    tcs_total = 0.0
    if db is not None:
        try:
            pipeline = [
                {"$group": {"_id": None, "total": {"$sum": "$tcs_amount"}}}
            ]
            agg = list(db.get_collection("ondc_settlements").aggregate(pipeline))
            tcs_total = round((agg[0]["total"] if agg else 0.0), 2)
        except Exception:
            pass

    return {
        "enabled": enabled,
        "env_gate": ondc_seller._env_ondc_enabled(),
        "simulated_reason": reason,
        "last_published_at": last_published_at,
        "last_item_count": last_item_count,
        "ondc_order_count": ondc_order_count,
        "tcs_total": tcs_total,
        "note": (
            "ONDC integration is DARK by default. "
            "Set IMS_ONDC_ENABLED=1 and configure the integration in Settings -> Integrations."
            if not enabled
            else "ONDC integration is ACTIVE."
        ),
    }


@router.post("/publish")
async def manual_publish(
    current_user: dict = Depends(require_roles(*_ADMIN_ROLES)),
) -> Dict[str, Any]:
    """Manually trigger catalog publish to the SNP.

    DARK when IMS_ONDC_ENABLED is off or creds are missing (returns
    mode=SIMULATED). Fail-soft: errors returned as structured {ok: false}.
    """
    db = _get_db()
    result = await ondc_seller.publish_catalog(db)
    return result
