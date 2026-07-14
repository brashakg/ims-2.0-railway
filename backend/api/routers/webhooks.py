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
- Over the per-vendor+IP rate limit → 429 with a generic detail (checked
  FIRST, before the body is read or any secret is looked up, so unsigned
  garbage can't be used to burn Mongo lookups + HMAC computes).
- Secret missing in DB → return 200 with `{"status":"skipped"}` so the
  vendor's retry queue treats this as "delivered, ignored". Returning
  4xx/5xx would cause Razorpay/Shopify/Shiprocket to retry every minute
  for 24 h — bad for everyone. Operators see the skip in `webhook_inbox`
  with `processed=false, skipped_reason=secret_not_configured`.
- Bad signature → 401 + `{"detail":"invalid signature"}`. Vendors that
  re-attempt on 401 will be silently swallowed but the legitimate
  delivery is rejected so a leaked URL can't be abused.
- Replayed delivery (same vendor event id already ingested) → 200 with
  `{"status":"duplicate"}` and NO re-dispatch. Webhooks must 2xx or the
  vendor retries forever; the original inbox row is the durable record.
- Persist failure (inbox collection/DB unavailable, or a non-duplicate insert
  error) → 503. We must NEVER ack-without-persist: a 2xx tells the vendor the
  delivery is permanently handled, so Shopify/Razorpay/Shiprocket never resend
  it and a real inbound order would be lost forever. 503 makes the vendor
  RETRY (Shopify backs off for ~48h). We only ACK 200 AFTER the row is durably
  written (or it's a verified duplicate whose original row is the record).
- Event dispatch failure → still 200 (the inbox row is ALREADY durable; NEXUS
  re-discovers unprocessed rows on its next tick, and re-acking would make the
  vendor resend a delivery we already persisted).

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

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Query

from agents import webhook_verify

logger = logging.getLogger(__name__)
router = APIRouter()

# ============================================================================
# WhatsApp inbound (Meta Business API) — CRM-14
# GET  /webhooks/whatsapp  -> Meta verify-token challenge
# POST /webhooks/whatsapp  -> receive inbound messages
# GET  /webhooks/whatsapp/conversations -> inbox list (role-gated)
#
# Auth model:
#   GET (challenge): PUBLIC — Meta sends a plain GET to verify the endpoint.
#   POST: signature verified via X-Hub-Signature-256 (HMAC-SHA256 of raw body
#         using WABA_APP_SECRET env var). If secret is unset, we ACCEPT the
#         delivery and skip verification (fail-soft / DARK).  This lets the
#         endpoint be registered in Meta Business Manager before creds land.
#   GET conversations: role-gated — caller must supply a valid IMS JWT.
#
# Fail-soft contract (same as other vendors above):
#   - WABA creds unset   -> 200 skipped; never 5xx to Meta's retry queue.
#   - Bad signature      -> 401 (Meta will retry; logs let you debug).
#   - Mongo down         -> 200, log warning (inbox is best-effort).
#   - Intent dispatch err-> 200, log warning (reply is best-effort).
# ============================================================================

_WABA_VERIFY_TOKEN = os.getenv("WABA_VERIFY_TOKEN", "")
_WABA_APP_SECRET = os.getenv("WABA_APP_SECRET", "")
# Default store for inbound-triggered follow-ups when we can't resolve a store.
_WABA_DEFAULT_STORE_ID = os.getenv("WABA_DEFAULT_STORE_ID", "HQ")


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
# Endpoint-level rate limiting — per vendor+IP sliding window
# ============================================================================
# Why: every unsigned garbage POST used to cost a Mongo `integrations` lookup
# plus an HMAC compute; the only cover was main.py's global in-memory
# 120/min/IP limiter (per-process — 4 workers means ~480/min effective — and
# it resets on every restart). This limiter is checked FIRST in every
# receiver — before the body is read and before any secret lookup — and goes
# through the shared cache seam (api/services/cache.py): Redis-backed when
# configured, so the window is shared across workers and survives restarts;
# in-memory fallback otherwise, which is no worse than today's global limiter.
# ============================================================================

_WEBHOOK_RATE_WINDOW_SECONDS = 60
_WEBHOOK_RATE_LIMIT_DEFAULT = 60  # per vendor+IP per minute


def _webhook_rate_limit_per_min() -> int:
    """Requests allowed per vendor+IP per minute. Env-overridable via
    WEBHOOK_RATE_LIMIT_PER_MIN; read at call time so ops can tune without a
    deploy-time code change. Garbage / non-positive values fall back safely."""
    raw = os.getenv("WEBHOOK_RATE_LIMIT_PER_MIN", "")
    try:
        return max(1, int(raw)) if raw else _WEBHOOK_RATE_LIMIT_DEFAULT
    except (TypeError, ValueError):
        return _WEBHOOK_RATE_LIMIT_DEFAULT


def _client_ip(request: Request) -> str:
    """Client IP for rate-limit bucketing. Reuses main.py's trusted-proxy-aware
    extractor (Railway sits behind a proxy, so X-Forwarded-For handling must
    match the global limiter's) with a plain socket fallback."""
    try:
        from api.main import _extract_client_ip

        return _extract_client_ip(request)
    except Exception:
        return request.client.host if request.client else "unknown"


def _check_webhook_rate_limit(vendor: str, client_ip: str) -> bool:
    """Sliding-window limiter keyed per vendor+IP via the shared cache seam.
    Returns True when the request is allowed. Fail-open on any cache error —
    a broken cache must never take the receivers down (main.py's global
    limiter still applies as outer cover). The stamp list is bounded by the
    limit itself (we stop appending once over), and the key TTL equals the
    window so idle buckets expire on their own."""
    try:
        from api.services.cache import cache

        key = f"webhook_rl:{vendor}:{client_ip}"
        now = time.time()
        cutoff = now - _WEBHOOK_RATE_WINDOW_SECONDS
        stamps = cache.get(key)
        if not isinstance(stamps, list):
            stamps = []
        stamps = [t for t in stamps if isinstance(t, (int, float)) and t > cutoff]
        if len(stamps) >= _webhook_rate_limit_per_min():
            return False
        stamps.append(now)
        cache.set(key, stamps, ttl=_WEBHOOK_RATE_WINDOW_SECONDS)
        return True
    except Exception:  # noqa: BLE001
        return True


def _enforce_webhook_rate_limit(request: Request, vendor: str) -> None:
    """Raise 429 when the vendor+IP bucket is over its per-minute budget.
    Called FIRST in every receiver. The detail string deliberately reveals
    nothing about limits, windows, or backing stores."""
    ip = _client_ip(request)
    if not _check_webhook_rate_limit(vendor, ip):
        logger.warning(
            "[WEBHOOKS] rate limit exceeded vendor=%s ip=%s", vendor, ip
        )
        raise HTTPException(status_code=429, detail="rate limit exceeded")


# ============================================================================
# Event-id replay dedupe
# ============================================================================
# Vendor delivery-id headers — when present we dedupe on them so a replayed,
# correctly-signed envelope inside the timestamp window (webhook_verify.
# is_replay allows ~5 min) can't be re-ingested as a second inbox row and
# re-dispatched (the Razorpay reconcile hook reads the most recent unprocessed
# inbox row, so replay duplicates would feed payment reconciliation).
# Shiprocket sends no delivery-id header (x-shiprocket-event is the event
# TYPE, shared by many deliveries) so it keeps timestamp-window-only cover —
# same as today. This is receiver-level and additive: shopify_ingest keeps its
# own order-id + webhook-id idempotency layers untouched.
# ============================================================================

_EVENT_ID_HEADERS = {
    "razorpay": "x-razorpay-event-id",
    "shopify": "x-shopify-webhook-id",
}


def _is_duplicate_key_error(exc: Exception) -> bool:
    """True when `exc` is a (pymongo) DuplicateKeyError. Name-based check
    first so test fakes without pymongo installed still match (same pattern
    as base_repository / order_repository)."""
    if exc.__class__.__name__ == "DuplicateKeyError":
        return True
    try:
        from pymongo.errors import DuplicateKeyError as _DKE

        return isinstance(exc, _DKE)
    except Exception:  # noqa: BLE001
        return False


# ============================================================================
# DB access — direct, no repository layer (one collection, three writes)
# ============================================================================

# Sentinel so we can distinguish "conn has no `.db` attribute" (a bare db-like
# object / test fake) from "conn.db is None" (Mongo genuinely unreachable)
# WITHOUT ever truth-testing a pymongo object (see _get_db for why that matters).
_NO_DB_ATTR = object()


def _get_db():
    """Return the live pymongo Database, or None when Mongo is unreachable.

    ROOT-CAUSE (P0, silent webhook data-loss): the previous body was
    `return getattr(d, "db", None) or d`. `d` is the DatabaseConnection
    singleton and `d.db` is the *connected pymongo Database*. PyMongo makes
    `bool(Database)` raise `NotImplementedError` (a deliberate guard against
    the `if db:` mistake), so the `or` evaluated `bool(<Database>)`, which
    raised, was swallowed by the `except Exception` below, and `_get_db()`
    returned None. Every caller then read None and SILENTLY skipped its write
    — the `webhook_inbox` insert among them — while the receiver still returned
    200 "received". A real inbound order was acknowledged-and-dropped. This is
    the same pymongo-truthiness trap that broke GRN/expense/HR uploads
    (file_store) and DB-backed product categories (see products.py:63-66).

    Fix: never `or`/`and` a pymongo Database or Collection. Read the `.db`
    property directly and compare with `is None`. `.db` returns the connected
    Database, or None if the connect() inside the property failed — in which
    case we propagate None so the caller treats storage as unavailable.
    """
    try:
        from database.connection import get_db as _gd

        conn = _gd()
        if conn is None:
            return None
        # conn is the DatabaseConnection singleton in prod; `.db` is the
        # connected pymongo Database (or None if Mongo is down). Some test
        # fakes ARE the db-like object and expose no `.db` — return those
        # as-is. Use a sentinel + `is` checks so we NEVER call bool() on a
        # pymongo object.
        database = getattr(conn, "db", _NO_DB_ATTR)
        if database is _NO_DB_ATTR:
            return conn
        return database  # real pymongo Database, or None if unreachable
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
        # Replay dedupe — UNIQUE partial index on (vendor, event_id) so a
        # replayed delivery carrying the same vendor event id is physically
        # impossible to double-insert even under a multi-worker race.
        # PARTIAL: only docs that actually carry an event_id string, so
        # vendors without a delivery-id header (and all legacy rows) are
        # unaffected. Mirrors shopify_ingest.ensure_shopify_order_index.
        try:
            coll.create_index(
                [("vendor", 1), ("event_id", 1)],
                unique=True,
                partialFilterExpression={"event_id": {"$type": "string"}},
                name="uniq_webhook_event_id",
            )
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
    secret: Optional[str] = None
    db = _get_db()
    if db is not None:
        try:
            coll = db.get_collection("integrations")
            doc = coll.find_one({"type": vendor.lower()})
            cfg = (doc or {}).get("config") or {}
            # BUG-155 parity: the Settings hub Fernet-encrypts webhook_secret at
            # rest (cred_crypto.SENSITIVE_FIELDS), so compare HMACs against the
            # DECRYPTED value. decrypt_config is a passthrough on legacy plaintext
            # rows; on any decrypt error fall back to the raw value (fail-soft).
            try:
                from ..services.cred_crypto import decrypt_config

                cfg = decrypt_config(cfg)
            except Exception:  # noqa: BLE001
                pass
            secret = cfg.get("webhook_secret") or None
        except Exception as e:
            logger.debug(f"[WEBHOOKS] secret lookup failed for {vendor}: {e}")
            secret = None
    # Shopify env fallback: a custom app's webhook HMAC signing key IS its API
    # secret key (== the OAuth client secret used by shopify_auth). When the
    # integrations doc carries no explicit webhook_secret, use the app secret
    # already on the server so inbound HMAC verification works without anyone
    # pasting a key. Mirrors the #916 auth-fix philosophy (env-first creds).
    if not secret and vendor.lower() == "shopify":
        secret = (
            os.getenv("SHOPIFY_CLIENT_SECRET")
            or os.getenv("SHOPIFY_API_SECRET")
            or None
        )
    return secret


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

      0. Rate-limit check (per vendor+IP). Over budget → 429. FIRST —
         before the body is read and before any secret lookup, so unsigned
         garbage can't burn Mongo lookups + HMAC computes.
      1. Read RAW body (HMAC depends on the unparsed bytes).
      2. Look up secret. Missing → 200 skipped (vendor must not retry).
      3. Verify signature. Bad → 401.
      4. Parse JSON.
      5. Replay-check via payload['event_timestamp'] (best-effort).
      6. Event-id dedupe (vendors that send a delivery-id header).
         Already ingested → 200 duplicate, no re-dispatch.
      7. Persist inbox doc (unique partial index backstops the race).
      8. Dispatch event. Returns webhook_id.
      9. 200.
    """
    _enforce_webhook_rate_limit(request, vendor)

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

    # Event-id replay dedupe — only for vendors that send a delivery-id
    # header, and only AFTER the signature verified (an attacker without the
    # secret can't use forged ids to suppress legitimate deliveries). The
    # find_one is the fast path; the unique partial index on
    # (vendor, event_id) is the hard backstop under a multi-worker race.
    event_id_header = _EVENT_ID_HEADERS.get(vendor)
    event_id: Optional[str] = None
    if event_id_header:
        event_id = request.headers.get(event_id_header) or None

    coll = _get_inbox_collection()

    # DATA-LOSS SAFETY (never ack-and-drop): if we cannot reach the inbox
    # collection we must NOT return 200. A 2xx tells the vendor the delivery is
    # permanently handled, so Shopify/Razorpay/Shiprocket will NEVER resend it —
    # a real inbound order would be lost forever. Return 503 so the vendor
    # RETRIES (Shopify backs off for ~48h; the others similarly). We only ever
    # ACK 200 AFTER the row is durably persisted, or when it's a verified
    # duplicate (its original row is already the durable record). The "no secret
    # configured -> 200 skipped" and "replay window -> 200 skipped" paths above
    # are deliberate, safe skips and are unaffected.
    if coll is None:
        logger.error(
            "[WEBHOOKS] %s: inbox collection unavailable — returning 503 so the "
            "vendor retries (refusing to ack-and-drop an unpersisted delivery)",
            vendor,
        )
        raise HTTPException(status_code=503, detail="storage temporarily unavailable")

    if event_id:
        try:
            existing = coll.find_one({"vendor": vendor, "event_id": event_id})
        except Exception:  # noqa: BLE001
            existing = None
        if existing is not None:
            # 200, not 4xx: webhooks must be ACKed or the vendor retries
            # forever. The original inbox row is the durable record; we do
            # NOT re-dispatch.
            logger.warning(
                "[WEBHOOKS] %s: duplicate delivery event_id=%s ignored "
                "(already ingested)",
                vendor,
                event_id,
            )
            return {"status": "duplicate", "vendor": vendor, "event_id": event_id}

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
        "event_id": event_id,
    }

    try:
        coll.insert_one(dict(inbox_doc))
    except Exception as e:
        if event_id and _is_duplicate_key_error(e):
            # Race backstop: a concurrent worker ingested the same
            # delivery between our pre-check and this insert. ACK it and
            # do NOT re-dispatch — the winner's row is the record.
            logger.warning(
                "[WEBHOOKS] %s: duplicate delivery event_id=%s ignored "
                "(unique index race backstop)",
                vendor,
                event_id,
            )
            return {
                "status": "duplicate",
                "vendor": vendor,
                "event_id": event_id,
            }
        # Genuine persist failure (Mongo write error that isn't a dup). The row
        # is NOT durable, so we must NOT ack — a 200 here would drop the event.
        # Return 503 so the vendor retries; the failure is logged loud.
        logger.error(f"[WEBHOOKS] inbox insert failed for {vendor}: {e}")
        raise HTTPException(status_code=503, detail="storage temporarily unavailable")

    # The row is now durably persisted. ONLY now do we dispatch + ACK 200.
    # Dispatch the event so NEXUS picks it up immediately / on its next tick.
    try:
        from agents.registry import dispatch_event

        await dispatch_event(
            "webhook.received",
            {"webhook_id": webhook_id, "vendor": vendor},
            source="webhooks_router",
        )
    except Exception as e:
        # The inbox row is already durable — NEXUS's hourly tick re-discovers
        # unprocessed rows — so a dispatch hiccup must NOT fail the request
        # (that would make the vendor resend a delivery we already persisted).
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
    return _MSG91_STATUS_MAP.get(
        str(raw or "").strip().lower(), str(raw or "").upper() or "UNKNOWN"
    )


@router.post("/msg91/delivery")
async def receive_msg91_delivery(request: Request):
    """MSG91 WhatsApp/SMS delivery-report webhook.

    Verifies the HMAC signature, then advances `delivery_status` on the
    notification_logs row whose `provider_msg_id` / `provider_id` matches the
    DLR's request id. Stub-level handler: it does the lookup + status advance
    and records the raw DLR; it does not (yet) fan out further events.

    Fail-soft like the other receivers: over rate limit -> 429, missing
    secret -> 200 skipped (so MSG91 won't hammer its retry queue), bad
    signature -> 401, Mongo down -> 200.
    """
    _enforce_webhook_rate_limit(request, "msg91")

    raw_body = await request.body()
    sig = request.headers.get("X-MSG91-Signature") or request.headers.get(
        "x-msg91-signature"
    )

    secret = _load_secret("msg91")
    if not secret:
        logger.info(
            "[WEBHOOKS] msg91: no webhook_secret configured -- skipping verification"
        )
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
    request_id = _first(
        body_obj, "request_id", "requestId", "messageId", "message_id"
    ) or _first(data_obj, "request_id", "requestId", "messageId", "message_id")
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
    """Razorpay webhook receiver. Signed via X-Razorpay-Signature.

    After the standard ingest pipeline (HMAC verify + inbox persist +
    event dispatch), attempts a fail-soft UPI auto-reconcile for
    payment.captured events: matches the payment to an IMS order by
    order_number (carried in the UPI tn= note) and records the payment.
    DARK when Razorpay creds are not configured in `integrations`.
    A reconcile failure never affects the 200 response to Razorpay.
    """
    result = await _ingest(
        request,
        vendor="razorpay",
        verifier=webhook_verify.verify_razorpay,
        signature_header_name="X-Razorpay-Signature",
    )

    # POS-6: UPI auto-reconcile on payment.captured.
    # The payload was already parsed inside _ingest and persisted to the
    # inbox.  Re-read the body here by going through the inbox row
    # (we cannot re-read the request body after _ingest drained it).
    # Instead, _ingest returns the payload via the inbox doc -- but to
    # keep the pattern simple we rely on the event that was dispatched
    # to NEXUS for full processing, and add a lightweight inline hook
    # only for the simple "match by order_number + amount" case.
    #
    # We pass the parsed webhook payload via a background best-effort
    # path.  Fail-soft: any error is caught + logged; never raises.
    # Gated on a FRESH ingest: a duplicate delivery (event-id dedupe) or a
    # skipped one wrote no new inbox row, so re-running the hook would just
    # re-read the ORIGINAL unprocessed row and re-feed reconciliation —
    # exactly the replay double-count this hardening closes.
    if result.get("status") == "received":
        try:
            _reconcile_razorpay_payment_bg(request)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[WEBHOOKS] razorpay reconcile hook skipped: %s", exc)

    return result


def _reconcile_razorpay_payment_bg(request: Request) -> None:
    """Best-effort UPI reconcile for Razorpay payment.captured.

    The payload has already been consumed by _ingest.  We cannot re-read
    the raw request body, so we look up the most recent unprocessed inbox
    row for razorpay (a reasonable proxy for the just-ingested event).
    Fail-soft: any error is logged + swallowed.  DARK when creds absent.
    """
    try:
        db = _get_db()
        if db is None:
            return
        inbox = db.get_collection("webhook_inbox")
        if inbox is None:
            return
        # Find the most recent razorpay inbox row that we just ingested.
        doc = inbox.find_one(
            {"vendor": "razorpay", "processed": False},
            sort=[("received_at", -1)],
        )
        if not doc:
            return
        payload = doc.get("payload") or {}
        event_type = str(payload.get("event") or "").lower()
        if event_type not in ("payment.captured", "payment.authorized"):
            return

        payment = (payload.get("payload") or {}).get("payment") or {}
        entity = payment.get("entity") or {}
        if not entity:
            return

        # Razorpay carries the UPI tn= note under description / notes.order_ref.
        notes = entity.get("notes") or {}
        order_ref = (
            notes.get("order_ref")
            or notes.get("order_number")
            or entity.get("description")
            or ""
        )
        if not order_ref:
            return

        # Resolve the IMS order_id from the order_number.
        orders_coll = db.get_collection("orders")
        if orders_coll is None:
            return
        order = orders_coll.find_one({"order_number": order_ref})
        if not order:
            logger.debug(
                "[WEBHOOKS] razorpay reconcile: no order for ref=%s", order_ref
            )
            return

        from ..services.upi_qr import reconcile_upi_payment

        reconcile_upi_payment(db, order.get("order_id") or "", entity)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WEBHOOKS] razorpay reconcile bg failed: %s", exc)


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
# WhatsApp inbound — CRM-14
# ============================================================================


def _verify_waba_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """
    Meta sends X-Hub-Signature-256: sha256=<hex>.
    We compute HMAC-SHA256 of the raw body with the app secret and compare.
    Pure function, fail-soft -> False on any error.
    """
    try:
        if not raw_body or not signature_header or not secret:
            return False
        expected_prefix = "sha256="
        if not signature_header.startswith(expected_prefix):
            return False
        claimed_hex = signature_header[len(expected_prefix):]
        actual = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(actual, claimed_hex.lower())
    except Exception as e:
        logger.debug("[WA_INBOUND] signature verify error: %s", e)
        return False


def _get_wa_conversations_collection():
    """Return the whatsapp_conversations collection or None."""
    db = _get_db()
    if db is None:
        return None
    try:
        coll = db.get_collection("whatsapp_conversations")
        # TTL: keep conversation threads for 180 days.
        try:
            coll.create_index("last_message_at", expireAfterSeconds=180 * 24 * 3600)
        except Exception:
            pass
        # Lookup by normalised phone.
        try:
            coll.create_index("phone", unique=True)
        except Exception:
            pass
        return coll
    except Exception as e:
        logger.warning("[WA_INBOUND] whatsapp_conversations collection unavailable: %s", e)
        return None


def _upsert_conversation(
    phone: str,
    customer_id: Optional[str],
    customer_name: Optional[str],
    message_doc: Dict[str, Any],
) -> None:
    """Upsert the per-customer conversation thread in whatsapp_conversations. Fail-soft."""
    coll = _get_wa_conversations_collection()
    if coll is None:
        return
    try:
        now = datetime.now(timezone.utc)
        digits = "".join(c for c in phone if c.isdigit())
        phone_key = digits[-10:] if len(digits) >= 10 else digits
        coll.update_one(
            {"phone": phone_key},
            {
                "$set": {
                    "phone": phone_key,
                    "phone_e164": phone,
                    "customer_id": customer_id,
                    "customer_name": customer_name or "Unknown",
                    "last_message_at": now,
                },
                "$push": {
                    "messages": {
                        "$each": [message_doc],
                        "$slice": -200,  # keep last 200 messages per thread
                    }
                },
                "$setOnInsert": {"created_at": now, "needs_human": False},
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning("[WA_INBOUND] conversation upsert failed: %s", e)


def _extract_message_parts(body: Dict[str, Any]) -> list[Dict[str, Any]]:
    """
    Parse the Meta webhook payload and return a flat list of message dicts.
    Meta nests: body.entry[].changes[].value.messages[].
    Returns [] when the payload has no messages (e.g. status updates).
    """
    messages = []
    try:
        for entry in body.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                for msg in value.get("messages") or []:
                    # Resolve sender phone from contacts[] array (Meta canonical).
                    contacts = value.get("contacts") or []
                    sender_name = None
                    if contacts:
                        sender_name = (contacts[0].get("profile") or {}).get("name")
                    text = ""
                    button_payload = None
                    msg_type = msg.get("type", "text")
                    if msg_type == "text":
                        text = (msg.get("text") or {}).get("body", "")
                    elif msg_type == "interactive":
                        inter = msg.get("interactive") or {}
                        if "button_reply" in inter:
                            button_payload = inter["button_reply"].get("id")
                            text = inter["button_reply"].get("title", "")
                        elif "list_reply" in inter:
                            button_payload = inter["list_reply"].get("id")
                            text = inter["list_reply"].get("title", "")
                    elif msg_type == "button":
                        # Template quick-reply button
                        button_payload = (msg.get("button") or {}).get("payload")
                        text = (msg.get("button") or {}).get("text", "")
                    messages.append(
                        {
                            "wa_message_id": msg.get("id"),
                            "from_phone": msg.get("from"),
                            "sender_name": sender_name,
                            "type": msg_type,
                            "text": text,
                            "button_payload": button_payload,
                            "timestamp": msg.get("timestamp"),
                            "received_at": datetime.now(timezone.utc).isoformat(),
                            "direction": "inbound",
                        }
                    )
    except Exception as e:
        logger.warning("[WA_INBOUND] message extraction failed: %s", e)
    return messages


@router.get("/whatsapp")
async def whatsapp_verify_challenge(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """
    Meta webhook verification challenge (GET).
    Meta sends: hub.mode=subscribe, hub.verify_token=<our token>, hub.challenge=<string>.
    We must echo hub.challenge back as plain text.

    FAIL-SOFT: if WABA_VERIFY_TOKEN is unset we echo the challenge anyway (so
    you can register the endpoint before the env var lands).  If the token IS
    set and it doesn't match, we return 403.
    """
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="invalid hub.mode")

    # Read the verify token fresh from the Settings -> Integrations hub
    # (type=meta_whatsapp) first, env fallback -- so a Save takes effect live.
    from ..services.integration_config import get_whatsapp_config

    _verify_token = get_whatsapp_config().get("verify_token", "")
    if _verify_token:
        if hub_verify_token != _verify_token:
            logger.warning(
                "[WA_INBOUND] verify_token mismatch; expected=%s got=%s",
                _verify_token[:4] + "...",
                str(hub_verify_token)[:4] + "...",
            )
            raise HTTPException(status_code=403, detail="forbidden")
    else:
        logger.info(
            "[WA_INBOUND] verify_token not set -- echoing challenge without token check"
        )

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(content=hub_challenge or "")


@router.post("/whatsapp")
async def receive_whatsapp_inbound(request: Request):
    """
    Meta inbound message webhook (POST).

    Steps:
      0. Rate-limit check (per vendor+IP). Over budget -> 429.
      1. Read raw body.
      2. Verify X-Hub-Signature-256 (skip if WABA_APP_SECRET unset).
      3. Parse payload -> extract messages.
      4. For each message: upsert conversation thread + dispatch intent.
      5. Return 200 (never 5xx to Meta).
    """
    _enforce_webhook_rate_limit(request, "whatsapp")

    raw_body = await request.body()
    sig = (
        request.headers.get("X-Hub-Signature-256")
        or request.headers.get("x-hub-signature-256")
        or ""
    )

    from ..services.integration_config import get_whatsapp_config

    _app_secret = get_whatsapp_config().get("app_secret", "")
    if _app_secret:
        if not sig:
            raise HTTPException(status_code=401, detail="invalid signature")
        if not _verify_waba_signature(raw_body, sig, _app_secret):
            logger.warning("[WA_INBOUND] bad X-Hub-Signature-256")
            raise HTTPException(status_code=401, detail="invalid signature")
    else:
        # SEC-WEBHOOK-WHATSAPP-FAILOPEN: with no app secret configured we CANNOT
        # authenticate the sender, so an attacker could POST forged inbound
        # messages and trigger outbound dispatch_intent replies. Fail CLOSED --
        # ack 200 (so Meta doesn't retry-storm) but DO NOT process the payload or
        # dispatch anything. Matches this module's documented contract
        # (processed=false, skipped_reason=secret_not_configured).
        logger.warning(
            "[WA_INBOUND] WABA app_secret not configured -- skipping inbound "
            "processing (fail-closed; no signature to verify a forged sender)."
        )
        return {
            "status": "received",
            "messages_processed": 0,
            "skipped": True,
            "skipped_reason": "secret_not_configured",
        }

    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, ValueError) as e:
        logger.warning("[WA_INBOUND] body parse error: %s", e)
        return {"status": "received", "messages_processed": 0}

    messages = _extract_message_parts(body)
    if not messages:
        # Status update or other non-message event -- ack and exit.
        return {"status": "received", "messages_processed": 0}

    results = []
    for msg in messages:
        phone = msg.get("from_phone") or ""
        text = msg.get("text") or ""
        button_payload = msg.get("button_payload")

        # Lazy import to avoid circular at module load time.
        try:
            from ..services.whatsapp_intents import dispatch_intent, _lookup_customer_by_phone

            customer = _lookup_customer_by_phone(phone)
            customer_id = customer.get("customer_id") if customer else None
            customer_name = (
                customer.get("name") or customer.get("full_name") if customer else msg.get("sender_name")
            )

            # Persist message to conversation thread.
            _upsert_conversation(phone, customer_id, customer_name, msg)

            intent_result = await dispatch_intent(
                phone=phone,
                text=text,
                button_payload=button_payload,
                store_id=_WABA_DEFAULT_STORE_ID,
            )
            results.append(intent_result)
            logger.info(
                "[WA_INBOUND] phone=...%s intent=%s reply_sent=%s",
                phone[-4:] if len(phone) >= 4 else phone,
                intent_result.get("intent"),
                intent_result.get("reply_sent"),
            )
        except Exception as e:
            logger.error("[WA_INBOUND] message processing error: %s", e, exc_info=True)
            results.append({"error": str(e), "phone": phone[-4:] if len(phone) >= 4 else ""})

    return {"status": "received", "messages_processed": len(messages), "results": results}


@router.get("/whatsapp/conversations")
async def list_whatsapp_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    needs_human: Optional[bool] = Query(None),
):
    """
    WhatsApp inbox: list conversation threads (most recent first).
    Role-gated: requires a valid IMS JWT with SUPERADMIN, ADMIN, or STORE_MANAGER.
    Read-only v1 -- no reply composition in this endpoint.
    """
    # Role gate -- inline check (mirrors the pattern in other routers).
    try:
        from .auth import get_current_user as _get_user
        from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

        auth_header = request.headers.get("Authorization") or ""
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="not authenticated")
        token = auth_header.split(" ", 1)[1]
        from .auth import decode_token as _decode
        payload = _decode(token)
        roles = payload.get("roles") or []
        allowed = {"SUPERADMIN", "ADMIN", "STORE_MANAGER"}
        if not any(r in allowed for r in roles):
            raise HTTPException(status_code=403, detail="forbidden")
    except HTTPException:
        raise
    except Exception as e:
        logger.debug("[WA_INBOUND] auth check failed: %s", e)
        raise HTTPException(status_code=401, detail="not authenticated")

    coll = _get_wa_conversations_collection()
    if coll is None:
        return {"conversations": [], "total": 0, "limit": limit, "offset": offset}

    try:
        filt: Dict[str, Any] = {}
        if needs_human is not None:
            filt["needs_human"] = needs_human

        total = coll.count_documents(filt)
        cursor = (
            coll.find(filt, {"messages": {"$slice": -20}})
            .sort("last_message_at", -1)
            .skip(offset)
            .limit(limit)
        )
        convs = []
        for doc in cursor:
            doc.pop("_id", None)
            convs.append(doc)
        return {
            "conversations": convs,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error("[WA_INBOUND] conversation list failed: %s", e)
        return {"conversations": [], "total": 0, "limit": limit, "offset": offset}


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
