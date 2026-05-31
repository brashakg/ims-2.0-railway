"""
IMS 2.0 — Inbound webhook receivers (Phase I-2)
==================================================
Three open routes — `/webhooks/razorpay`, `/webhooks/shopify`,
`/webhooks/shiprocket` — that accept signed POSTs from the upstream
vendor, verify the HMAC, persist the envelope to a `webhook_inbox`
collection, and dispatch a `webhook.received` event so NEXUS can drain
the work asynchronously.

Auth model:
- NO bearer token — the HMAC signature IS the auth. Bringing the
  vendor's request through `get_current_user` is impossible (they have
  no IMS credentials) and gating it is unnecessary because every
  request is signature-verified against a per-vendor secret stored in
  the `integrations` collection.

Fail-soft contract:
- Secret missing in DB → return 200 with `{"status":"skipped"}` so the
  vendor's retry queue treats this as "delivered, ignored". Returning
  4xx/5xx would cause Razorpay/Shopify/Shiprocket to retry every minute
  for 24 h — bad for everyone. Operators see the skip in `webhook_inbox`
  with `processed=false, skipped_reason=secret_not_configured`.
- Bad signature → 401 + `{"detail":"invalid signature"}`. Vendors that
  re-attempt on 401 will be silently swallowed but the legitimate
  delivery is rejected so a leaked URL can't be abused.
- Mongo down → log + 200 (we don't want vendor retries to overwhelm us).
- Event dispatch failure → still 200 (the inbox row is the durable record;
  NEXUS can sweep it on next tick).

The inbox doc shape:

    {
      "webhook_id": "<uuid>",
      "vendor": "razorpay" | "shopify" | "shiprocket",
      "received_at": <utc datetime>,
      "headers": {...selected headers...},
      "payload": {...parsed json body...},
      "raw_body_size": <int>,
      "processed": false,
      "processed_at": None,
      "skipped_reason": None | str,
    }

NEXUS subscribes to `webhook.received` and reads the doc by `webhook_id`
to do the actual provider-specific work.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from agents import webhook_verify

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Headers we keep on the inbox doc — limit cardinality so we never persist
# a wall of vendor cookies / x-forwarded-for chains. Lower-cased on read.
# ============================================================================

_KEEP_HEADERS = frozenset(
    {
        "content-type",
        "user-agent",
        "x-razorpay-signature",
        "x-razorpay-event-id",
        "x-shopify-hmac-sha256",
        "x-shopify-topic",
        "x-shopify-shop-domain",
        "x-shopify-webhook-id",
        "x-shiprocket-signature",
        "x-shiprocket-event",
    }
)


def _filter_headers(request: Request) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in request.headers.items():
        if k.lower() in _KEEP_HEADERS:
            out[k.lower()] = v
    return out


# ============================================================================
# DB access — direct, no repository layer (one collection, three writes)
# ============================================================================


def _get_db():
    """Same pattern as the rest of the routers."""
    try:
        from database.connection import get_db as _gd

        d = _gd()
        if d is None:
            return None
        return getattr(d, "db", None) or d
    except Exception as e:
        logger.debug(f"[WEBHOOKS] _get_db failed: {e}")
        return None


def _get_inbox_collection():
    db = _get_db()
    if db is None:
        return None
    try:
        coll = db.get_collection("webhook_inbox")
        # Ensure a TTL on received_at — 30 days. Idempotent + cheap.
        try:
            coll.create_index("received_at", expireAfterSeconds=30 * 24 * 3600)
        except Exception:
            pass
        # Lookup index — we look up by webhook_id from NEXUS
        try:
            coll.create_index("webhook_id", unique=False)
        except Exception:
            pass
        return coll
    except Exception as e:
        logger.warning(f"[WEBHOOKS] webhook_inbox collection unavailable: {e}")
        return None


def _load_secret(vendor: str) -> Optional[str]:
    """
    Pull the per-vendor `webhook_secret` from the `integrations` doc.
    Mirrors `nexus_providers._load_integration_config` shape.
    """
    db = _get_db()
    if db is None:
        return None
    try:
        coll = db.get_collection("integrations")
        doc = coll.find_one({"type": vendor.lower()})
        if not doc:
            return None
        cfg = doc.get("config") or {}
        return cfg.get("webhook_secret") or None
    except Exception as e:
        logger.debug(f"[WEBHOOKS] secret lookup failed for {vendor}: {e}")
        return None


# ============================================================================
# Common pipeline
# ============================================================================


async def _ingest(
    request: Request,
    vendor: str,
    verifier,
    signature_header_name: str,
) -> Dict[str, Any]:
    """
    Shared receiver pipeline for all three vendors. Steps:

      1. Read RAW body (HMAC depends on the unparsed bytes).
      2. Look up secret. Missing → 200 skipped (vendor must not retry).
      3. Verify signature. Bad → 401.
      4. Parse JSON.
      5. Replay-check via payload['event_timestamp'] (best-effort).
      6. Persist inbox doc.
      7. Dispatch event. Returns webhook_id.
      8. 200.
    """
    raw_body = await request.body()
    sig = request.headers.get(signature_header_name) or request.headers.get(
        signature_header_name.lower()
    )

    secret = _load_secret(vendor)
    if not secret:
        # Vendor's perspective: 200 OK, don't retry. Operator's perspective:
        # plain log line they'll grep for.
        logger.info(
            f"[WEBHOOKS] {vendor}: no webhook_secret configured — skipping verification"
        )
        return {"status": "skipped", "reason": "secret_not_configured"}

    if not sig:
        raise HTTPException(status_code=401, detail="invalid signature")

    if not verifier(raw_body, sig, secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    # Parse JSON only AFTER signature verify (defence in depth — we never
    # parse untrusted blobs without proving the sender knew the secret).
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, ValueError):
        # Signature was good but body isn't JSON. Persist the raw bytes as
        # a string so NEXUS / forensics can still see what came in.
        payload = {
            "_unparseable_body": raw_body[:1024].decode("utf-8", errors="replace")
        }

    # Replay window — purely best-effort. Vendors don't always set a
    # consistent timestamp field; we look in three common spots.
    ts = (
        (payload.get("event_timestamp") if isinstance(payload, dict) else None)
        or (payload.get("created_at") if isinstance(payload, dict) else None)
        or (payload.get("timestamp") if isinstance(payload, dict) else None)
        or ""
    )
    replay_flag = False
    try:
        if ts:
            replay_flag = webhook_verify.is_replay(str(ts))
    except Exception:
        replay_flag = False
    if replay_flag:
        logger.warning(
            f"[WEBHOOKS] {vendor}: stale event timestamp {ts} — outside replay window"
        )
        return {"status": "skipped", "reason": "replay_window_exceeded"}

    webhook_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    inbox_doc = {
        "webhook_id": webhook_id,
        "vendor": vendor,
        "received_at": now,
        "headers": _filter_headers(request),
        "payload": payload,
        "raw_body_size": len(raw_body or b""),
        "processed": False,
        "processed_at": None,
        "skipped_reason": None,
    }

    coll = _get_inbox_collection()
    if coll is not None:
        try:
            coll.insert_one(dict(inbox_doc))
        except Exception as e:
            # Mongo down — the receiver MUST stay green so vendors don't
            # back up their retry queue. Log loud, swallow.
            logger.error(f"[WEBHOOKS] inbox insert failed for {vendor}: {e}")

    # Dispatch the event so NEXUS picks it up on its next tick / immediately.
    try:
        from agents.registry import dispatch_event

        await dispatch_event(
            "webhook.received",
            {"webhook_id": webhook_id, "vendor": vendor},
            source="webhooks_router",
        )
    except Exception as e:
        # Already in inbox — NEXUS's hourly tick can re-discover unprocessed
        # rows. Don't fail the request.
        logger.warning(f"[WEBHOOKS] dispatch_event failed for {vendor}: {e}")

    return {
        "status": "received",
        "webhook_id": webhook_id,
        "vendor": vendor,
    }


# ============================================================================
# Endpoints
# ============================================================================


# ============================================================================
# MSG91 delivery-report (DLR) receiver -- advances delivery_status on the
# matching notification_logs row past SENT. Authenticated by HMAC signature
# (X-MSG91-Signature) against the `msg91` integration's webhook_secret, exactly
# like the vendor receivers above (the signature IS the auth; MSG91 has no IMS
# bearer token).
# ============================================================================

# MSG91 DLR status -> canonical delivery_status on notification_logs.
# MSG91 reports both numeric codes and string statuses depending on channel;
# we accept either. Anything unknown is recorded verbatim (upper-cased) so we
# never silently swallow a status we don't yet model.
_MSG91_STATUS_MAP = {
    "1": "DELIVERED",
    "delivered": "DELIVERED",
    "read": "READ",
    "2": "FAILED",
    "failed": "FAILED",
    "undelivered": "FAILED",
    "rejected": "FAILED",
    "blocked": "FAILED",
    "sent": "SENT",
    "submitted": "SENT",
}


def _canonical_dlr_status(raw: str) -> str:
    return _MSG91_STATUS_MAP.get(str(raw or "").strip().lower(), str(raw or "").upper() or "UNKNOWN")


@router.post("/msg91/delivery")
async def receive_msg91_delivery(request: Request):
    """MSG91 WhatsApp/SMS delivery-report webhook.

    Verifies the HMAC signature, then advances `delivery_status` on the
    notification_logs row whose `provider_msg_id` / `provider_id` matches the
    DLR's request id. Stub-level handler: it does the lookup + status advance
    and records the raw DLR; it does not (yet) fan out further events.

    Fail-soft like the other receivers: missing secret -> 200 skipped (so MSG91
    won't hammer its retry queue), bad signature -> 401, Mongo down -> 200.
    """
    raw_body = await request.body()
    sig = request.headers.get("X-MSG91-Signature") or request.headers.get(
        "x-msg91-signature"
    )

    secret = _load_secret("msg91")
    if not secret:
        logger.info("[WEBHOOKS] msg91: no webhook_secret configured -- skipping verification")
        return {"status": "skipped", "reason": "secret_not_configured"}

    if not sig or not webhook_verify.verify_msg91(raw_body, sig, secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, ValueError):
        payload = {}

    # MSG91 nests DLR data differently per product; pull the common fields with
    # several fallbacks rather than assuming one exact shape.
    def _first(d: Dict[str, Any], *keys):
        for k in keys:
            v = d.get(k)
            if v:
                return v
        return None

    body_obj = payload if isinstance(payload, dict) else {}
    data_obj = body_obj.get("data") if isinstance(body_obj.get("data"), dict) else {}
    request_id = (
        _first(body_obj, "request_id", "requestId", "messageId", "message_id")
        or _first(data_obj, "request_id", "requestId", "messageId", "message_id")
    )
    raw_status = (
        _first(body_obj, "status", "deliveryStatus", "delivery_status", "event")
        or _first(data_obj, "status", "deliveryStatus", "delivery_status", "event")
        or ""
    )
    canonical = _canonical_dlr_status(raw_status)

    updated = 0
    db = _get_db()
    if db is not None and request_id:
        try:
            coll = db.get_collection("notification_logs")
            update = {
                "delivery_status": canonical,
                "dlr_received_at": datetime.now(timezone.utc).isoformat(),
                "dlr_raw_status": str(raw_status),
            }
            if canonical == "DELIVERED":
                update["delivered_at"] = update["dlr_received_at"]
            res = coll.update_many(
                {"$or": [{"provider_msg_id": request_id}, {"provider_id": request_id}]},
                {"$set": update},
            )
            updated = getattr(res, "modified_count", 0) or 0
        except Exception as e:
            # Stay green so MSG91 doesn't retry-storm; the miss is logged.
            logger.error(f"[WEBHOOKS] msg91 DLR update failed: {e}")

    if request_id and updated == 0:
        logger.info(
            "[WEBHOOKS] msg91 DLR for request_id=%s status=%s matched no rows",
            request_id,
            canonical,
        )

    return {
        "status": "received",
        "vendor": "msg91",
        "request_id": request_id,
        "delivery_status": canonical,
        "updated": updated,
    }


@router.post("/razorpay")
async def receive_razorpay(request: Request):
    """Razorpay webhook receiver. Signed via X-Razorpay-Signature."""
    return await _ingest(
        request,
        vendor="razorpay",
        verifier=webhook_verify.verify_razorpay,
        signature_header_name="X-Razorpay-Signature",
    )


@router.post("/shopify")
async def receive_shopify(request: Request):
    """Shopify webhook receiver. Signed via X-Shopify-Hmac-Sha256."""
    return await _ingest(
        request,
        vendor="shopify",
        verifier=webhook_verify.verify_shopify,
        signature_header_name="X-Shopify-Hmac-Sha256",
    )


@router.post("/shiprocket")
async def receive_shiprocket(request: Request):
    """Shiprocket webhook receiver. Signed via X-Shiprocket-Signature."""
    return await _ingest(
        request,
        vendor="shiprocket",
        verifier=webhook_verify.verify_shiprocket,
        signature_header_name="X-Shiprocket-Signature",
    )


# ============================================================================
# Light health check — handy for vendor "test webhook" buttons that just
# want to confirm DNS / TLS without hitting the real signed flow.
# ============================================================================


@router.get("/health")
async def webhooks_health():
    return {
        "status": "ok",
        "module": "webhooks",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
