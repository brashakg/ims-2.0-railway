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
- Secret genuinely not configured → 200 `{"status":"skipped"}` so the vendor's
  retry queue treats this as "delivered, ignored". Returning 4xx/5xx would
  cause Razorpay/Shopify/Shiprocket to retry every minute for 24 h, and no
  amount of retrying fixes a missing config value. Operators see the skip in
  `webhook_inbox` with `processed=true, skipped_reason=secret_not_configured,
  signature_verified=false` — metadata only, NO payload, because without a
  secret we cannot authenticate the sender (see `_record_unverifiable`).
- Secret LOOKUP FAILED (Mongo unreachable / raised) → 503, NOT the skip above.
  These were indistinguishable until this PR: `_load_secret` returned a bare
  None for both, so a Mongo blip 2xx-dropped correctly-signed Razorpay and
  Shiprocket deliveries — real payments and their GST — with no record and no
  resend. `_load_secret` now returns `(secret, lookup_ok)`.
- Bad signature → 401 + `{"detail":"invalid signature"}`. Vendors that
  re-attempt on 401 will be silently swallowed but the legitimate
  delivery is rejected so a leaked URL can't be abused.
- Replayed delivery (same vendor event id, or the same signed bytes,
  already ingested) → 200 with `{"status":"duplicate"}` and NO second inbox
  row. Webhooks must 2xx or the vendor retries forever; the original row is
  the durable record. This is ALSO how a vendor's retry of a delivery we
  already stored is handled — acked, never double-booked. The ONE case where
  a duplicate does more than ack: if the matched row is still
  `processed=false` (its original dispatch failed), we re-dispatch it, because
  nothing else in the system will (see below).
- Delivery older than the staleness cap → 200 `{"status":"skipped",
  "reason":"delivery_too_old"}`, but the row IS persisted with
  `processed=true, skipped_reason="delivery_too_old"`. A correctly-signed
  delivery is never discarded without a durable record.
- Persist failure (inbox collection/DB unavailable, or a non-duplicate insert
  error) → 503. We must NEVER ack-without-persist: a 2xx tells the vendor the
  delivery is permanently handled, so Shopify/Razorpay/Shiprocket never resend
  it and a real inbound order would be lost forever. 503 makes the vendor
  RETRY (Shopify backs off for ~48h). We only ACK 200 AFTER the row is durably
  written (or it's a verified duplicate whose original row is the record).
- Event dispatch failure → still 200 (the inbox row is ALREADY durable, and
  re-acking would make the vendor resend a delivery we already persisted).
  NOTE, because an earlier version of this docstring claimed otherwise:
  NOTHING SWEEPS `webhook_inbox` FOR `processed=false`.
  `NexusAgent._handle_inbox_webhook` is reachable only from
  `on_event("webhook.received")`; `_do_background_work` iterates
  INTEGRATION_SCHEDULES and never touches this collection. A dispatch failure
  is therefore logged at ERROR, and the only automatic recovery is a later
  vendor retry, which the duplicate path re-dispatches.

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
      "event_id": None | str,          # vendor delivery id header, if any
      "body_fingerprint": "sha256:..." # content-bound replay key
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
        # Delivery clock — the ONLY timestamp the freshness check may use.
        # Kept on the row so an operator can audit a staleness decision.
        "x-shopify-triggered-at",
        "x-shiprocket-signature",
        "x-shiprocket-event",
        # MSG91 delivery-report receivers. The HMAC signature is safe to keep;
        # the token alternatives (authkey / x-msg91-token / ?token=) are the
        # SECRET itself and are deliberately NOT in this set.
        "x-msg91-signature",
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
# Replay dedupe — two keys, both backed by unique partial indexes
# ============================================================================
# 1. VENDOR DELIVERY ID (`_EVENT_ID_HEADERS`). Stable across a vendor's own
#    retries of the same delivery, so it is what makes a retry idempotent:
#    retry -> 200 duplicate, no second inbox row, no second dispatch, no
#    double-booked order/GST invoice. Shiprocket sends no delivery-id header
#    (x-shiprocket-event is the event TYPE, shared by many deliveries).
#
# 2. BODY FINGERPRINT (`webhook_verify.body_fingerprint`). SHA-256 over the
#    exact bytes the HMAC signed, scoped by vendor + event-type header. This
#    is the load-bearing anti-replay control, because unlike the id header it
#    is INSIDE the signature's coverage: an attacker replaying a captured
#    delivery cannot alter one byte without invalidating the HMAC, so the
#    fingerprint matches an existing row and is rejected even if they rotate
#    X-Shopify-Webhook-Id to a fresh value, and regardless of how old the
#    capture is. It also covers the vendors that send no delivery id at all
#    (Shiprocket), which previously had NO replay cover beyond a timestamp
#    window that never fired.
#
#    SCOPED, THOUGH -- and an earlier version of this comment omitted the
#    qualifier. The fingerprint mixes in the canonical scope, so rejection is
#    guaranteed WITHIN A SCOPE, not absolutely: one captured signed body can
#    still be accepted once per scope, bounded by the closed set (22 for
#    Shopify -- 16 exact topics + 5 family buckets + UNKNOWN_SCOPE, which
#    covers both an absent header and any unrecognised topic; 1 elsewhere).
#    That bound caps replay AMPLIFICATION. It does not
#    make routing authenticated -- NEXUS still selects the money handler from
#    the raw unsigned topic header, so a captured body can be relabelled into
#    a handler it was never meant for. Binding the topic to the signature is
#    a separate change.
#
#    WHAT THE SHAPE GUARD DOES AND DOES NOT COVER. The shared classifier
#    (shopify_ingest.order_payload_refusal) stops a relabel in the BOOKING
#    direction ONLY -- a non-order body cannot be relabelled orders/create and
#    minted into an IMS order + GST invoice serial. It says nothing about the
#    DESTRUCTIVE direction, which is still open: nexus routes orders/delete to
#    shopify_order_delete.handle_shopify_order_delete, which reads the top-level
#    id with NO shape assertion. A captured, validly-signed orders/create body --
#    whose top-level id IS a real live order id -- replayed as
#    X-Shopify-Topic: orders/delete VOIDS that order, and fingerprint dedupe does
#    not stop it because orders/delete is a DISTINCT canonical scope from the
#    scope the capture was first seen in. Do not read this paragraph as "the
#    relabel problem is handled"; only half of it is.
#
# Both are receiver-level and additive: shopify_ingest keeps its own
# order-id + webhook-id idempotency layers untouched.
#
# Retention: the inbox TTL (webhook_verify.DEDUPE_RETENTION_SECONDS, 30 days
# on received_at) is the dedupe window, and the delivery staleness cap is
# pinned to the same constant.
#
# READ THIS BEFORE LEANING ON THAT ALIGNMENT: it is a real age bound for
# RAZORPAY ONLY, whose clock rides inside the HMAC-signed envelope. Shopify's
# X-Shopify-Triggered-At is an unsigned header an attacker can rewrite or
# simply OMIT (no header -> no clock -> no cap), and Shiprocket publishes no
# clock at all. For those two vendors there is NO enforceable age bound and
# the fingerprint/id dedupe carries 100% of the replay defence. Accepting a
# delivery we cannot date is the deliberate fail-safe direction — dropping a
# real GST-bearing order is worse — but do not mistake the alignment for a
# universal guarantee.
# ============================================================================

_EVENT_ID_HEADERS = {
    "razorpay": "x-razorpay-event-id",
    "shopify": "x-shopify-webhook-id",
}

# Vendor event-TYPE headers. Used only to scope the body fingerprint so that
# two different topics carrying a byte-identical body are not collapsed into
# one dedupe key (e.g. Shopify orders/paid vs orders/updated).
_EVENT_TYPE_HEADERS = {
    "shopify": "x-shopify-topic",
    "shiprocket": "x-shiprocket-event",
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


def _ensure_index(coll, keys, **kwargs) -> bool:
    """create_index that NEVER raises but is never silent either.

    These builds used to be `except Exception: pass` with zero logging — the
    only fully silent failure in this module. That matters: if a pre-index
    deploy-window race writes two rows sharing (vendor, body_fingerprint),
    every later build fails E11000 forever, the hard multi-worker replay
    backstop is permanently absent, and nobody ever finds out. The receiver
    must still serve (the find_one fast paths keep working), so we log and
    carry on rather than failing the request.
    """
    try:
        coll.create_index(keys, **kwargs)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[WEBHOOKS] webhook_inbox index %s could not be ensured: %s — "
            "dedupe falls back to the find_one fast path (no multi-worker "
            "race backstop) until this is resolved",
            kwargs.get("name") or keys,
            e,
        )
        return False


def _get_inbox_collection(db=None):
    db = db if db is not None else _get_db()
    if db is None:
        return None
    try:
        coll = db.get_collection("webhook_inbox")
        # Ensure a TTL on received_at. Idempotent + cheap. The retention is
        # imported from webhook_verify because the delivery staleness cap is
        # pinned to the SAME number -- see the invariant note there. Changing
        # one must change the other, so there is only one number.
        _ensure_index(
            coll,
            "received_at",
            expireAfterSeconds=webhook_verify.DEDUPE_RETENTION_SECONDS,
        )
        # Lookup index — we look up by webhook_id from NEXUS
        _ensure_index(coll, "webhook_id", unique=False)
        # Replay dedupe — UNIQUE partial index on (vendor, event_id) so a
        # replayed delivery carrying the same vendor event id is physically
        # impossible to double-insert even under a multi-worker race.
        # PARTIAL: only docs that actually carry an event_id string, so
        # vendors without a delivery-id header (and all legacy rows) are
        # unaffected. Mirrors shopify_ingest.ensure_shopify_order_index.
        _ensure_index(
            coll,
            [("vendor", 1), ("event_id", 1)],
            unique=True,
            partialFilterExpression={"event_id": {"$type": "string"}},
            name="uniq_webhook_event_id",
        )
        # Content-bound replay dedupe — UNIQUE partial index on
        # (vendor, body_fingerprint). The fingerprint hashes the exact bytes
        # the HMAC signed, so a replayed delivery physically cannot be
        # double-inserted even if the attacker rewrites every header, and
        # even under a multi-worker race. PARTIAL so pre-existing rows
        # (which carry no fingerprint) are untouched and the index builds
        # without a backfill.
        _ensure_index(
            coll,
            [("vendor", 1), ("body_fingerprint", 1)],
            unique=True,
            partialFilterExpression={"body_fingerprint": {"$type": "string"}},
            name="uniq_webhook_body_fingerprint",
        )
        # SEPARATE namespace for rows we could not authenticate (no secret
        # configured). It collapses repeat identical posts onto one row exactly
        # like the index above, but it MUST NOT be the same field: an
        # unverifiable row sharing the authenticated key would answer
        # "duplicate" to the operator's own recovery resend once the secret is
        # finally configured. See _record_unverifiable.
        _ensure_index(
            coll,
            [("vendor", 1), ("unverified_fingerprint", 1)],
            unique=True,
            partialFilterExpression={"unverified_fingerprint": {"$type": "string"}},
            name="uniq_webhook_unverified_fingerprint",
        )
        return coll
    except Exception as e:
        logger.warning(f"[WEBHOOKS] webhook_inbox collection unavailable: {e}")
        return None


def record_pulled_order(
    db,
    payload: Dict[str, Any],
    *,
    webhook_id: str,
    topic: str,
    skipped_reason: Optional[str] = None,
    handler_error: Optional[str] = None,
) -> bool:
    """Record an order the NEXUS catch-up pull fetched from Shopify's Admin API
    (a delivery that never arrived as a webhook) in the SAME inbox, with the
    same row vocabulary, so the online-orders FAILED queue and POST
    /online-store/orders/remap/{id} work for pulled orders exactly as for
    received ones. Distinguishing marks: source='shopify_pull', a deterministic
    _id (one row per Shopify order + updated_at; a re-pull just refreshes it)
    and signature_verified=False -- the trust basis is our own authenticated
    API call, not an HMAC, so no reader may count these as verified
    deliveries. Fail-soft: never raises; True when the row was written."""
    coll = _get_inbox_collection(db)
    if coll is None:
        return False
    now = datetime.now(timezone.utc)
    row = {
        "webhook_id": webhook_id,
        "vendor": "shopify",
        "received_at": now,
        "headers": {"x-shopify-topic": topic, "x-shopify-webhook-id": webhook_id},
        "payload": payload,
        "raw_body_size": 0,
        "processed": True,
        "processed_at": now,
        "skipped_reason": skipped_reason,
        "handler_error": handler_error,
        "event_id": webhook_id,
        "signature_verified": False,
        "source": "shopify_pull",
    }
    try:
        coll.update_one({"_id": webhook_id}, {"$set": row}, upsert=True)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[WEBHOOKS] could not record pulled shopify order %s: %s", webhook_id, e)
        return False


def _load_secret(vendor: str) -> tuple[Optional[str], bool]:
    """Pull the per-vendor `webhook_secret` from the `integrations` doc.

    Returns `(secret, lookup_ok)`.

    THE SECOND ELEMENT IS THE POINT. This used to return a bare Optional, so
    "this integration genuinely has no secret configured" and "the lookup
    BLEW UP / Mongo was unreachable" were the same value: None. The caller
    read that None as the former and returned 200
    {"status":"skipped","reason":"secret_not_configured"} with no inbox row —
    so a Mongo blip lasting a second or two would 2xx-drop a correctly-signed,
    first-time Razorpay or Shiprocket delivery (a real payment and its GST),
    the vendor would treat it as permanently handled and never resend, and the
    only trace was a logger.debug line. Shopify partially escaped through the
    env fallback below; the other two had nothing. Meanwhile the SAME Mongo
    outage a few lines later, at the inbox-collection check, correctly returned
    503 so the vendor retries.

    So: `lookup_ok=False` means "we could not determine whether a secret
    exists" and the caller MUST 503 rather than ack. It is True only when we
    actually reached the config (or resolved the secret from env), whether or
    not a secret was found.

    Never logs the secret value — only the vendor and, on failure, the error.
    """
    db = _get_db()
    secret: Optional[str] = None
    lookup_ok = True

    if db is None:
        # Not "no secret" — we never got to look. Storage is down.
        lookup_ok = False
    else:
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
            # ERROR, not debug: this branch now changes the HTTP status.
            logger.error(
                "[WEBHOOKS] %s: webhook_secret lookup FAILED (%s) — treating as "
                "storage-unavailable so the vendor retries, NOT as "
                "'no secret configured'",
                vendor,
                e,
            )
            secret = None
            lookup_ok = False

    # Shopify env fallback: a custom app's webhook HMAC signing key IS its API
    # secret key (== the OAuth client secret used by shopify_auth). When the
    # integrations doc carries no explicit webhook_secret, use the app secret
    # already on the server so inbound HMAC verification works without anyone
    # pasting a key. Mirrors the #916 auth-fix philosophy (env-first creds).
    # This also rescues the lookup-failure case: if the env secret is present
    # we can verify the signature without the DB, so the lookup outcome is
    # moot and we proceed normally.
    if not secret and vendor.lower() == "shopify":
        secret = (
            os.getenv("SHOPIFY_CLIENT_SECRET")
            or os.getenv("SHOPIFY_API_SECRET")
            or None
        )
        if secret:
            lookup_ok = True

    # MSG91 env fallback (same philosophy as the Shopify block above): the
    # delivery-report webhook secret can live on Railway as
    # MSG91_WEBHOOK_TOKEN, so the receivers verify without anyone pasting a
    # key into Settings. Also rescues the lookup-failure case identically.
    if not secret and vendor.lower() == "msg91":
        secret = os.getenv("MSG91_WEBHOOK_TOKEN") or None
        if secret:
            lookup_ok = True
    return secret, lookup_ok


# ============================================================================
# Common pipeline
# ============================================================================


async def _dispatch_webhook_received(webhook_id: str, vendor: str) -> bool:
    """Fire `webhook.received` for a durably-persisted inbox row.

    Returns True when THE PUBLISH CALL DID NOT RAISE — which is not the same
    as "NEXUS processed it", and is weaker than it looks: `registry.
    dispatch_event` catches every exception itself and never re-raises, so in
    practice this returns True even when the bus publish and its local
    fallback both failed. Treat `redispatched: true` as "we asked again", not
    as proof of delivery. Making this signal honest needs a dispatch_event
    variant that can report failure; that is its own change.

    Never raises: the row is already durable, so a dispatch hiccup must not
    fail the request (that would make the vendor resend a delivery we already
    stored).
    """
    try:
        from agents.registry import dispatch_event

        await dispatch_event(
            "webhook.received",
            {"webhook_id": webhook_id, "vendor": vendor},
            source="webhooks_router",
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(
            "[WEBHOOKS] dispatch_event failed for %s webhook_id=%s: %s — the row "
            "is durable but UNPROCESSED. Nothing sweeps webhook_inbox for "
            "processed=false, so it will only be drained if the vendor retries "
            "(which now re-dispatches) or an operator replays it by hand.",
            vendor,
            webhook_id,
            e,
        )
        return False


# How long a row may sit processed=false before a duplicate treats it as
# stranded rather than in-flight.
#
# WHAT THIS WINDOW IS AND IS NOT. It is NOT what protects the money, and an
# earlier version of this comment wrongly implied it was by claiming "the
# event-bus fan-out completes in milliseconds" — that describes the PUBLISH.
# The DRAIN is serialised per worker (_listen_loop awaits _dispatch_local
# inline, behind every other bus event) and map_shopify_order is synchronous,
# pymongo-heavy work, so a row can legitimately sit unprocessed well past a
# minute during a flash sale or a post-redeploy backlog. The number is a
# heuristic, not a bound.
#
# What actually prevents a double-booked order + GST invoice is downstream and
# CLAIM-FIRST: shopify_ingest._webhook_already_seen takes an atomic insert-claim
# on _id='shopify:<webhook-id>' and returns 'replayed' BEFORE any invoice serial
# is allocated, plus the orders.find_one({shopify_order_id}) guard and the
# unique partial index with its E11000 branch; refunds claim first on a unique
# shopify_refund_id. A premature re-dispatch therefore resolves to 'replayed',
# not to a second booking.
#
# So this window is belt-and-braces over those claims: it costs only latency of
# rescue, and it keeps the common case from generating pointless duplicate
# drains. Do not let anything come to depend on it as a guarantee.
_REDISPATCH_MIN_AGE_SECONDS = 60


def _is_stranded(existing: Dict[str, Any]) -> bool:
    """True when an inbox row's dispatch demonstrably never landed.

    Requires BOTH processed is false AND the row to be older than
    `_REDISPATCH_MIN_AGE_SECONDS` — see `_duplicate_response` for why the age
    check is not optional. Unparseable/missing `received_at` returns False:
    we would rather miss a rescue than risk a concurrent second drain.
    """
    if existing.get("processed") is not False:
        return False
    received_at = existing.get("received_at")
    if isinstance(received_at, str):
        from agents.webhook_verify import _parse_iso

        received_at = _parse_iso(received_at)
    if not isinstance(received_at, datetime):
        return False
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - received_at).total_seconds()
    return age >= _REDISPATCH_MIN_AGE_SECONDS


async def _duplicate_response(
    existing: Dict[str, Any],
    vendor: str,
    event_id: Optional[str],
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """ACK a duplicate delivery, re-dispatching if the original never drained.

    Why the re-dispatch: this module used to justify ACKing a failed dispatch
    with "NEXUS's hourly tick re-discovers unprocessed rows". IT DOES NOT —
    `NexusAgent._handle_inbox_webhook` is reachable only from
    `on_event("webhook.received")`, and `_do_background_work` iterates
    INTEGRATION_SCHEDULES and never queries webhook_inbox for
    {processed: False}. So a row whose dispatch failed just sat there. The
    vendor's retry used to be the accidental rescue; now that dedupe
    correctly rejects that retry, the rescue has to be deliberate.

    A row that is still processed=false is therefore re-dispatched here — but
    ONLY after a grace period, and that qualifier is load-bearing:

      * `dispatch_event` publishes through the event bus. With Redis
        configured (as in prod) the fan-out is ASYNCHRONOUS, so processed=false
        immediately after a 200 is the NORMAL in-flight state, not evidence of
        stranding. Re-dispatching on the flag alone would let a fast vendor
        retry run a second concurrent drain of the same row — trading a
        stranded row for double-processing on a money path.
      * `NexusAgent._handle_inbox_webhook` sets processed=true even when the
        vendor handler raises (it records `handler_error`). So a row that is
        still false after the grace window genuinely never reached NEXUS,
        which is the only case worth rescuing. A handler that failed is NOT
        re-run here; replaying a failed money handler on every duplicate
        would be worse than leaving it for an operator.

    Re-dispatch is otherwise safe: the drain is keyed on webhook_id, re-checks
    `processed` before doing any work, and the downstream ingest layers are
    idempotent on the business object id. We keep status="duplicate" (no
    second inbox row, and the Razorpay inline reconcile stays gated off).
    """
    webhook_id = existing.get("webhook_id")
    out: Dict[str, Any] = {
        "status": "duplicate",
        "vendor": vendor,
        "event_id": event_id,
        # Echoed so an operator (or Shopify's "Send test notification", whose
        # canned payload is byte-stable and so always dedupes after the first
        # send) can see WHICH row matched instead of reading it as a failure.
        "webhook_id": webhook_id,
    }
    if reason:
        out["reason"] = reason

    if webhook_id and _is_stranded(existing):
        logger.warning(
            "[WEBHOOKS] %s: duplicate matched a row still unprocessed after "
            "%ss (webhook_id=%s) — its dispatch never landed; re-dispatching "
            "so the delivery is not stranded",
            vendor,
            _REDISPATCH_MIN_AGE_SECONDS,
            webhook_id,
        )
        out["redispatched"] = await _dispatch_webhook_received(webhook_id, vendor)
    return out


def _record_unverifiable(
    request: Request, vendor: str, raw_body: bytes
) -> Dict[str, Any]:
    """Record a delivery we cannot authenticate because no secret is set.

    The module contract has always promised that operators see this skip in
    `webhook_inbox` with a `skipped_reason`. Nothing ever wrote such a row —
    the only value ever persisted was 'delivery_too_old' — so the promise was
    false and a misconfigured integration swallowed live orders leaving only a
    log line. This makes the promise true.

    WHAT IS DELIBERATELY NOT STORED: the payload. With no secret we cannot
    verify the sender, so anyone who learns the URL can post arbitrary bytes
    here. Persisting those bodies would turn the inbox into an unauthenticated
    blob store AND give the operator recovery surfaces (Re-map) a forgeable
    source. We keep only metadata — size, headers, a fingerprint — which is
    exactly what answers the operator's question ("deliveries ARE arriving and
    we are dropping them; go configure the secret") and nothing an attacker
    can weaponise. `signature_verified: False` is stamped so no future reader
    mistakes these rows for trusted data.

    Bounded: the fingerprint carries the unique index, so repeated identical
    posts collapse to ONE row, and the per-vendor+IP rate limit caps the rest.

    Fail-soft on purpose: if the inbox is unavailable we still ACK 200. A
    missing secret is a configuration gap that no amount of vendor retrying
    can fix, so 503-storming the vendor would be worse than a log line.
    """
    fingerprint = webhook_verify.body_fingerprint(
        vendor,
        raw_body,
        scope=webhook_verify.canonical_scope(
            vendor, request.headers.get(_EVENT_TYPE_HEADERS.get(vendor) or "") or ""
        ),
    )
    coll = _get_inbox_collection()
    if coll is None:
        logger.error(
            "[WEBHOOKS] %s: no webhook_secret configured AND the inbox is "
            "unavailable — this delivery leaves no record at all",
            vendor,
        )
        return {"status": "skipped", "reason": "secret_not_configured"}

    now = datetime.now(timezone.utc)
    try:
        coll.insert_one(
            {
                "webhook_id": str(uuid.uuid4()),
                "vendor": vendor,
                "received_at": now,
                "headers": _filter_headers(request),
                # No payload: unauthenticated content is never stored.
                "payload": None,
                "raw_body_size": len(raw_body or b""),
                "processed": True,
                "processed_at": now,
                "skipped_reason": "secret_not_configured",
                "signature_verified": False,
                "event_id": None,
                # DELIBERATELY *NOT* `body_fingerprint`. That field is the
                # AUTHENTICATED dedupe key, and writing an unverifiable row
                # into it made this row poison the very recovery it exists to
                # prompt: operator sees the row -> pastes the secret -> the
                # vendor resends the identical bytes, now correctly signed ->
                # the fingerprint dedupe matched THIS row and answered 200
                # duplicate. Never dispatched, payload never stored,
                # processed=True so _is_stranded would not rescue it, blocked
                # for the full 30-day TTL. Strictly worse than doing nothing,
                # and it hit exactly the two vendors with no env fallback:
                # Razorpay (payments) and Shiprocket (shipments).
                #
                # A separate field + its own unique index keeps repeats
                # collapsed onto one row without ever answering for a
                # verified delivery.
                "unverified_fingerprint": fingerprint,
            }
        )
    except Exception as e:  # noqa: BLE001
        if not _is_duplicate_key_error(e):
            logger.error(
                "[WEBHOOKS] %s: could not record the unverifiable delivery: %s",
                vendor,
                e,
            )
    return {"status": "skipped", "reason": "secret_not_configured"}


def _persist_skipped(
    coll,
    *,
    request: Request,
    vendor: str,
    payload: Any,
    raw_body: bytes,
    event_id: Optional[str],
    fingerprint: str,
    skipped_reason: str,
) -> Dict[str, Any]:
    """Durably record a correctly-signed delivery we deliberately will NOT
    process, then ACK it.

    Nothing correctly-signed may ever be discarded without a trace. The row
    carries processed=True (so no drain ever picks it up) plus the reason, and
    it keeps both dedupe keys so a vendor retry of the same delivery matches
    it instead of writing a second skip row.

    If the write fails we do NOT ack — 503 makes the vendor retry, exactly as
    for a failed ingest.
    """
    webhook_id = str(uuid.uuid4())
    doc = {
        "webhook_id": webhook_id,
        "vendor": vendor,
        "received_at": datetime.now(timezone.utc),
        "headers": _filter_headers(request),
        "payload": payload,
        "raw_body_size": len(raw_body or b""),
        "processed": True,
        "processed_at": datetime.now(timezone.utc),
        "skipped_reason": skipped_reason,
        "event_id": event_id,
        "body_fingerprint": fingerprint,
        # Stamped on EVERY verified row so the field is total across the
        # collection. It used to appear only on unverifiable rows, so a future
        # operator surface filtering {"signature_verified": True} would have
        # matched nothing and reported "no verified webhooks".
        "signature_verified": True,
    }
    try:
        coll.insert_one(dict(doc))
    except Exception as e:  # noqa: BLE001
        if _is_duplicate_key_error(e):
            # Already recorded (vendor retried the same stale delivery).
            return {
                "status": "skipped",
                "reason": skipped_reason,
                "vendor": vendor,
            }
        logger.error(
            "[WEBHOOKS] %s: could not persist the skipped (%s) delivery: %s — "
            "returning 503 rather than dropping it without a record",
            vendor,
            skipped_reason,
            e,
        )
        raise HTTPException(status_code=503, detail="storage temporarily unavailable")

    return {
        "status": "skipped",
        "reason": skipped_reason,
        "vendor": vendor,
        "webhook_id": webhook_id,
    }


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
      5. Compute both dedupe keys; resolve the inbox collection (503 if it
         is unavailable — never ack what we cannot store).
      6. Replay dedupe on the vendor delivery id AND on the body fingerprint.
         Already ingested → 200 duplicate, no second row (re-dispatched only
         if the matched row never drained). Runs BEFORE the staleness check
         so a retry of an already-recorded delivery resolves as a duplicate
         instead of writing a second skip row.
      7. Staleness cap on the DELIVERY's own timestamp (never on a business
         object's created_at). No delivery clock → no cap, delivery accepted.
         Over the cap → persist a skipped row, then 200.
      8. Persist inbox doc (unique partial indexes backstop the race).
      9. Dispatch event. Returns webhook_id.
     10. 200.
    """
    _enforce_webhook_rate_limit(request, vendor)

    raw_body = await request.body()
    sig = request.headers.get(signature_header_name) or request.headers.get(
        signature_header_name.lower()
    )

    secret, lookup_ok = _load_secret(vendor)
    if not lookup_ok:
        # We could not determine whether a secret exists — storage is down, not
        # "unconfigured". 503 so the vendor RETRIES, exactly like the
        # inbox-unavailable branch below. Acking here would 2xx-drop a real,
        # correctly-signed payment with no record and no resend.
        logger.error(
            "[WEBHOOKS] %s: cannot resolve the webhook secret (storage "
            "unavailable) — returning 503 so the vendor retries",
            vendor,
        )
        raise HTTPException(status_code=503, detail="storage temporarily unavailable")

    if not secret:
        # Genuinely unconfigured integration. Vendor's perspective: 200 OK,
        # don't retry — a config gap is not something a retry storm can fix.
        # Operator's perspective: a WARNING plus a durable inbox row, because
        # "we silently swallowed your live orders for three days" must be
        # discoverable in the data, not just in a log ring buffer.
        logger.warning(
            "[WEBHOOKS] %s: no webhook_secret configured — CANNOT verify this "
            "delivery; recording it as skipped and processing nothing",
            vendor,
        )
        return _record_unverifiable(request, vendor, raw_body)

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

    # Replay dedupe keys, computed only AFTER the signature verified (an
    # attacker without the secret can't use forged keys to suppress
    # legitimate deliveries).
    #   - event_id: the vendor's delivery id header, stable across the
    #     vendor's own retries. Absent for Shiprocket. STRIPPED, so a
    #     whitespace-padded id cannot dodge the (vendor, event_id) unique
    #     index the way an unnormalised value would.
    #   - fingerprint: SHA-256 over the exact signed bytes — always present,
    #     and unforgeable, so it is the control that actually holds.
    #     body_fingerprint normalises vendor + scope internally.
    # The find_one calls are the fast path; the unique partial indexes are
    # the hard backstop under a multi-worker race.
    event_id_header = _EVENT_ID_HEADERS.get(vendor)
    event_id: Optional[str] = None
    if event_id_header:
        event_id = (request.headers.get(event_id_header) or "").strip() or None

    event_type_header = _EVENT_TYPE_HEADERS.get(vendor)
    raw_event_type = (
        (request.headers.get(event_type_header) or "") if event_type_header else ""
    )
    # CLOSED ALLOWLIST, not the raw header. The header is unsigned, so feeding
    # it straight in let ONE signed body mint an unbounded number of dedupe
    # keys (5000 random topic strings -> 5000 keys); normalising its case
    # alone, as the first cut did, only closed the spelling axis.
    # canonical_scope maps it onto a fixed set: real topics keep their
    # identity, edit-only families share one bucket each, everything else
    # becomes "unknown".
    event_type = webhook_verify.canonical_scope(vendor, raw_event_type)
    if raw_event_type and event_type == webhook_verify.UNKNOWN_SCOPE:
        logger.warning(
            "[WEBHOOKS] %s: unrecognised event-type header %r — bucketing it as "
            "'%s' for dedupe (a real new vendor topic belongs in "
            "webhook_verify.SHOPIFY_TOPIC_ALLOWLIST)",
            vendor,
            raw_event_type[:80],
            webhook_verify.UNKNOWN_SCOPE,
        )
    fingerprint = webhook_verify.body_fingerprint(vendor, raw_body, scope=event_type)

    coll = _get_inbox_collection()

    # DATA-LOSS SAFETY (never ack-and-drop): if we cannot reach the inbox
    # collection we must NOT return 200. A 2xx tells the vendor the delivery is
    # permanently handled, so Shopify/Razorpay/Shiprocket will NEVER resend it —
    # a real inbound order would be lost forever. Return 503 so the vendor
    # RETRIES (Shopify backs off for ~48h; the others similarly). We only ever
    # ACK 200 AFTER the row is durably persisted, or when it's a verified
    # duplicate (its original row is already the durable record). The two skip
    # paths both leave a durable row of their own: "no secret configured"
    # writes a metadata-only unverifiable row, and the staleness skip below
    # writes a full one. A secret lookup that FAILED never reaches here — it
    # 503s above, so a DB blip can no longer masquerade as "unconfigured".
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
            # forever. The original inbox row is the durable record.
            logger.warning(
                "[WEBHOOKS] %s: duplicate delivery event_id=%s ignored "
                "(already ingested as webhook_id=%s)",
                vendor,
                event_id,
                existing.get("webhook_id"),
            )
            return await _duplicate_response(existing, vendor, event_id)

    # Content-bound dedupe. Catches a verbatim replay whose delivery-id
    # header was rotated, and gives Shiprocket (no delivery id at all) real
    # replay cover for the first time.
    try:
        existing_body = coll.find_one(
            {"vendor": vendor, "body_fingerprint": fingerprint}
        )
    except Exception:  # noqa: BLE001
        existing_body = None
    if existing_body is not None:
        logger.warning(
            "[WEBHOOKS] %s: duplicate delivery body fingerprint=%s ignored "
            "(already ingested as webhook_id=%s)",
            vendor,
            fingerprint,
            existing_body.get("webhook_id"),
        )
        return await _duplicate_response(
            existing_body, vendor, event_id, reason="duplicate_body"
        )

    # ------------------------------------------------------------------
    # Delivery staleness cap.
    #
    # P1 FIX: this used to read payload['event_timestamp'|'created_at'|
    # 'timestamp'] — i.e. the BUSINESS OBJECT's clock. For a Shopify order
    # `created_at` is when the CUSTOMER PLACED THE ORDER, so with the old
    # 300 s window every payment update / fulfillment / cancellation /
    # refund webhook about an order older than five minutes was classified
    # a "replay" and silently dropped, as was every vendor retry. Orders
    # placed more than 5 minutes before delivery were unprocessable.
    #
    # We now use the DELIVERY's own clock only (Shopify
    # X-Shopify-Triggered-At; Razorpay's signed envelope epoch). No delivery
    # clock -> no cap: we ACCEPT and let dedupe carry the replay defence,
    # because dropping a real GST-bearing order is the worse failure.
    #
    # The cap is pinned to the dedupe retention (30 d), so it can only bite
    # where dedupe genuinely cannot help — a delivery whose dedupe row has
    # already expired. An earlier cut of this fix used 7 days, which bought
    # nothing (dedupe covers everything under 30 d) and handed Razorpay a
    # new silent-drop path: its clock is INSIDE the HMAC, so unlike a
    # Shopify header it cannot be omitted, and an owner clicking Resend in
    # the Razorpay dashboard after a >7-day outage would have lost a
    # GST-bearing payment with no trace.
    #
    # And even here we do NOT drop silently: the row is persisted with
    # processed=True + skipped_reason so a correctly-signed delivery always
    # leaves a durable, greppable record. It is simply never dispatched.
    # ------------------------------------------------------------------
    delivery_ts: Optional[str] = None
    try:
        delivery_ts = webhook_verify.extract_delivery_timestamp(
            vendor, request.headers, payload
        )
    except Exception:  # noqa: BLE001
        delivery_ts = None
    stale_flag = False
    try:
        if delivery_ts:
            stale_flag = webhook_verify.is_stale_delivery(str(delivery_ts))
    except Exception:  # noqa: BLE001
        stale_flag = False
    if stale_flag:
        logger.error(
            "[WEBHOOKS] %s: delivery timestamp %s is older than the staleness "
            "cap (%ss) — recording it as skipped, NOT dispatching",
            vendor,
            delivery_ts,
            webhook_verify.delivery_max_age_seconds(),
        )
        return _persist_skipped(
            coll,
            request=request,
            vendor=vendor,
            payload=payload,
            raw_body=raw_body,
            event_id=event_id,
            fingerprint=fingerprint,
            skipped_reason="delivery_too_old",
        )

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
        "body_fingerprint": fingerprint,
        "signature_verified": True,
    }

    try:
        coll.insert_one(dict(inbox_doc))
    except Exception as e:
        # A duplicate-key error means a concurrent worker already persisted
        # this delivery — under EITHER unique index (event_id or
        # body_fingerprint). Either way the winner's row is the durable
        # record, so ACK and do not re-dispatch.
        if _is_duplicate_key_error(e):
            # Race backstop: a concurrent worker ingested the same delivery
            # between our pre-check and this insert. ACK and do NOT dispatch —
            # the winner owns the row and is dispatching it right now. (If the
            # winner's own dispatch then fails, the next vendor retry takes the
            # find_one path above and re-dispatches the unprocessed row.)
            logger.warning(
                "[WEBHOOKS] %s: duplicate delivery event_id=%s fingerprint=%s "
                "ignored (unique index race backstop)",
                vendor,
                event_id,
                fingerprint,
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
    # A dispatch failure must NOT fail the request (that would make the vendor
    # resend a delivery we already persisted, and the resend would now be
    # rejected as a duplicate anyway). It is logged at ERROR, and a later
    # vendor retry re-dispatches the still-unprocessed row via
    # _duplicate_response — there is NO background sweep to fall back on.
    await _dispatch_webhook_received(webhook_id, vendor)

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

# The MSG91 status map + canonicaliser used to live HERE. They moved to
# api.services.message_events (canonical_dlr_status / event_for) when the
# second MSG91 receiver below landed, so the two receivers share ONE
# interpretation of a delivery report (apply_delivery_report) instead of two
# copies that drift. Do not re-add a status map to this module.


def _first(d: Dict[str, Any], *keys):
    """First truthy value of `keys` in dict `d` (MSG91 nests DLR data
    differently per product; never assume one exact shape)."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return None


@router.post("/msg91/delivery")
async def receive_msg91_delivery(request: Request):
    """MSG91 WhatsApp/SMS delivery-report webhook (the pre-existing receiver).

    Verifies the HMAC signature, then hands the report to the ONE
    interpreter (message_events.apply_delivery_report): it advances
    `delivery_status` on the matching notification_logs row exactly as this
    handler always did, AND records the spine event in message_events --
    the same shape the channel receiver below writes.

    Fail-soft like the other receivers: over rate limit -> 429, missing
    secret -> 200 skipped (so MSG91 won't hammer its retry queue), bad
    signature -> 401, Mongo down -> 200.
    """
    _enforce_webhook_rate_limit(request, "msg91")

    raw_body = await request.body()
    sig = request.headers.get("X-MSG91-Signature") or request.headers.get(
        "x-msg91-signature"
    )

    secret, lookup_ok = _load_secret("msg91")
    if not lookup_ok:
        # Same rule as the vendor receivers: a lookup we could not complete is
        # storage-unavailable, not "unconfigured". 503 so MSG91 retries.
        logger.error(
            "[WEBHOOKS] msg91: cannot resolve the webhook secret (storage "
            "unavailable) -- returning 503 so the vendor retries"
        )
        raise HTTPException(status_code=503, detail="storage temporarily unavailable")
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

    from ..services.message_events import apply_delivery_report, canonical_dlr_status

    if request_id:
        applied = apply_delivery_report(
            provider_message_id=str(request_id),
            raw_status=raw_status,
            mobile=_first(body_obj, "mobile", "number", "telNum", "msisdn", "to")
            or _first(data_obj, "mobile", "number", "telNum", "msisdn", "to"),
            at=_first(body_obj, "timestamp", "date", "deliveredAt")
            or _first(data_obj, "timestamp", "date", "deliveredAt"),
        )
        canonical = applied["delivery_status"]
        updated = applied["logs_updated"]
    else:
        canonical = canonical_dlr_status(raw_status)
        updated = 0

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


# ============================================================================
# MSG91 "Webhook (New)" -- per-channel delivery-report / inbound receiver
# POST /api/v1/integrations/msg91/webhooks/{channel}
#
# Mounted as its OWN router (msg91_events_router) so main.py can put it at the
# /api/v1/integrations/msg91/webhooks prefix while everything reuses this
# module's building blocks (rate limit, secret loading, inbox persistence,
# fingerprint dedupe). Follows the /webhooks/shopify pattern: PUBLIC route,
# the signature/token IS the auth, answer fast (MSG91 gives 8s and retries
# 4-5 times on non-2xx), durable inbox row before the 200, fail-soft
# processing after it.
#
# Auth: X-MSG91-Signature HMAC (hex sha256, webhook_verify.verify_msg91) OR a
# shared token (MSG91's webhook UI can attach a header; we accept
# `x-msg91-token` / `authkey` headers or a `?token=` query param), both
# checked constant-time against the msg91 integration doc's webhook_secret
# with the MSG91_WEBHOOK_TOKEN env fallback (_load_secret).
#
# Processing is INLINE (a few Mongo writes), not dispatched to NEXUS: the
# inbox row is the durable audit record ("queue"), and everything after it is
# fail-soft -- a processing hiccup never turns into a 5xx retry storm.
# ============================================================================

_MSG91_CHANNELS = frozenset(
    {"sms", "whatsapp", "email", "voice", "rcs", "shorturl"}
)

msg91_events_router = APIRouter()


def _msg91_request_authenticated(
    request: Request, raw_body: bytes, secret: str
) -> bool:
    """True when the request proves knowledge of the msg91 webhook secret,
    either by HMAC signature or by shared token. Constant-time comparisons
    only; fail-closed on anything else."""
    try:
        sig = request.headers.get("x-msg91-signature") or ""
        if sig and webhook_verify.verify_msg91(raw_body, sig, secret):
            return True
        token = (
            request.headers.get("x-msg91-token")
            or request.headers.get("authkey")
            or request.query_params.get("token")
            or ""
        )
        if token and hmac.compare_digest(token.strip(), secret):
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _iter_msg91_events(payload: Any) -> list:
    """Flatten an MSG91 webhook body into event dicts:
    {provider_message_id, raw_status, mobile, at, url}.

    MSG91's shapes vary per channel and product generation, so this walks the
    three shapes seen in their docs -- a flat dict (Webhook New), a list of
    request blocks each carrying a per-number `report` list (classic SMS DLR),
    and a Meta-cloud envelope with entry[].changes[].value.statuses[]
    (WhatsApp) -- extracting leniently. Anything unusable is skipped; the
    enrichment inside record_message_event fills gaps from notification_logs.
    """
    out: list = []

    def _flat(d: Dict[str, Any], inherited_id=None):
        data_obj = d.get("data") if isinstance(d.get("data"), dict) else {}
        pid = (
            _first(d, "request_id", "requestId", "messageId", "message_id", "msg_id")
            or _first(
                data_obj, "request_id", "requestId", "messageId", "message_id", "msg_id"
            )
            or inherited_id
        )
        status = _first(
            d, "status", "deliveryStatus", "delivery_status", "event", "eventName"
        ) or _first(
            data_obj, "status", "deliveryStatus", "delivery_status", "event", "eventName"
        )
        if not (pid and status):
            return
        out.append(
            {
                "provider_message_id": str(pid),
                "raw_status": status,
                "mobile": _first(d, "mobile", "number", "telNum", "msisdn", "to", "recipient")
                or _first(data_obj, "mobile", "number", "telNum", "msisdn", "to", "recipient"),
                "at": _first(d, "timestamp", "date", "deliveredAt", "updated_at")
                or _first(data_obj, "timestamp", "date", "deliveredAt", "updated_at"),
                "url": _first(d, "url", "short_url", "shortUrl", "link")
                or _first(data_obj, "url", "short_url", "shortUrl", "link"),
            }
        )

    def _one(item: Any):
        if not isinstance(item, dict):
            return
        # Meta-cloud envelope (WhatsApp channel): statuses[] per change.
        for entry in item.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes") or []:
                value = (change or {}).get("value") or {}
                for st in value.get("statuses") or []:
                    if not isinstance(st, dict):
                        continue
                    if not (st.get("id") and st.get("status")):
                        continue
                    out.append(
                        {
                            "provider_message_id": str(st["id"]),
                            "raw_status": st["status"],
                            "mobile": st.get("recipient_id"),
                            "at": st.get("timestamp"),
                            "url": None,
                        }
                    )
        if item.get("entry"):
            return
        # Classic SMS DLR: request block with a per-number report list.
        reports = item.get("report")
        if isinstance(reports, list):
            rid = _first(item, "requestId", "request_id", "messageId", "message_id")
            for rep in reports:
                if isinstance(rep, dict):
                    _flat(rep, inherited_id=rid)
            return
        _flat(item)

    if isinstance(payload, list):
        for item in payload:
            _one(item)
    else:
        _one(payload)
    return out


def _iter_meta_referrals(payload: Any) -> list:
    """(phone, referral) pairs from Meta-shaped inbound messages carrying
    Click-to-WhatsApp ad metadata. [] when none."""
    pairs: list = []
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        if not isinstance(item, dict):
            continue
        for entry in item.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes") or []:
                value = (change or {}).get("value") or {}
                for msg in value.get("messages") or []:
                    if not isinstance(msg, dict):
                        continue
                    referral = msg.get("referral")
                    if isinstance(referral, dict) and referral and msg.get("from"):
                        pairs.append((msg.get("from"), referral))
    return pairs


def _process_msg91_events(channel: str, payload: Any) -> Dict[str, int]:
    """Inline, fail-soft processing of a persisted msg91 channel delivery:
    feed each report through the ONE interpreter, and stamp CTWA attribution
    from WhatsApp inbound referrals. Never raises."""
    counts = {"events_recorded": 0, "ctwa_stamped": 0}
    try:
        from ..services.message_events import (
            apply_delivery_report,
            stamp_ctwa_attribution,
        )

        for ev in _iter_msg91_events(payload):
            applied = apply_delivery_report(
                provider_message_id=ev["provider_message_id"],
                raw_status=ev["raw_status"],
                channel=channel,
                mobile=ev.get("mobile"),
                at=ev.get("at"),
                url=ev.get("url"),
            )
            if applied.get("event_result") == "recorded":
                counts["events_recorded"] += 1

        if channel == "whatsapp":
            for phone, referral in _iter_meta_referrals(payload):
                if stamp_ctwa_attribution(phone, referral):
                    counts["ctwa_stamped"] += 1

        # Voice-escalation IVR: a pressed "1" on the escalation call
        # acknowledges the matching task (mirrors POST /tasks/{id}/acknowledge
        # + a history row naming channel=voice_ivr). Fail-soft like the rest.
        if channel == "voice":
            from ..services.voice_escalation import apply_voice_acks

            counts["voice_acks"] = apply_voice_acks(payload)
    except Exception as exc:  # noqa: BLE001
        logger.error("[WEBHOOKS] msg91 %s event processing failed: %s", channel, exc)
    return counts


@msg91_events_router.post("/{channel}")
async def receive_msg91_channel(channel: str, request: Request):
    """MSG91 per-channel webhook receiver (delivery reports, click events,
    WhatsApp inbound). See the block comment above for the contract."""
    channel = (channel or "").strip().lower()
    if channel not in _MSG91_CHANNELS:
        raise HTTPException(status_code=404, detail="unknown channel")

    _enforce_webhook_rate_limit(request, f"msg91_{channel}")

    raw_body = await request.body()

    secret, lookup_ok = _load_secret("msg91")
    if not lookup_ok:
        logger.error(
            "[WEBHOOKS] msg91/%s: cannot resolve the webhook secret (storage "
            "unavailable) -- returning 503 so the vendor retries",
            channel,
        )
        raise HTTPException(status_code=503, detail="storage temporarily unavailable")
    if not secret:
        # Same contract as the vendor receivers: ACK so MSG91 does not
        # retry-storm, but record a metadata-only inbox row so the gap is
        # discoverable in data, and process NOTHING unauthenticated.
        logger.warning(
            "[WEBHOOKS] msg91/%s: no webhook secret configured -- CANNOT verify "
            "this delivery; recording it as skipped and processing nothing",
            channel,
        )
        return _record_unverifiable(request, "msg91", raw_body)

    if not _msg91_request_authenticated(request, raw_body, secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, ValueError):
        payload = {
            "_unparseable_body": raw_body[:1024].decode("utf-8", errors="replace")
        }

    # The channel comes from the PATH (unsigned), but it is bounded by the
    # closed 6-value allowlist above -- same bounded-amplification argument as
    # the Shopify topic scopes. Scoping the fingerprint by channel keeps one
    # body posted to two channels from collapsing into one dedupe key.
    fingerprint = webhook_verify.body_fingerprint("msg91", raw_body, scope=channel)

    coll = _get_inbox_collection()
    if coll is None:
        logger.error(
            "[WEBHOOKS] msg91/%s: inbox collection unavailable -- returning 503 "
            "so the vendor retries (refusing to ack-and-drop)",
            channel,
        )
        raise HTTPException(status_code=503, detail="storage temporarily unavailable")

    try:
        existing = coll.find_one({"vendor": "msg91", "body_fingerprint": fingerprint})
    except Exception:  # noqa: BLE001
        existing = None
    if existing is not None:
        # Processing is inline and idempotent (the spine dedupes per event),
        # so a retry of an already-recorded delivery just ACKs.
        return {
            "status": "duplicate",
            "vendor": "msg91",
            "channel": channel,
            "webhook_id": existing.get("webhook_id"),
        }

    webhook_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    try:
        coll.insert_one(
            {
                "webhook_id": webhook_id,
                "vendor": "msg91",
                "channel": channel,
                "received_at": now,
                "headers": _filter_headers(request),
                "payload": payload,
                "raw_body_size": len(raw_body or b""),
                "processed": False,
                "processed_at": None,
                "skipped_reason": None,
                "event_id": None,
                "body_fingerprint": fingerprint,
                "signature_verified": True,
            }
        )
    except Exception as e:  # noqa: BLE001
        if _is_duplicate_key_error(e):
            return {"status": "duplicate", "vendor": "msg91", "channel": channel}
        logger.error("[WEBHOOKS] msg91/%s inbox insert failed: %s", channel, e)
        raise HTTPException(status_code=503, detail="storage temporarily unavailable")

    # Durable row written -- everything after this is fail-soft and the
    # response is 200 regardless (the inbox row is the recovery surface).
    counts = _process_msg91_events(channel, payload)
    try:
        coll.update_one(
            {"webhook_id": webhook_id},
            {
                "$set": {
                    "processed": True,
                    "processed_at": datetime.now(timezone.utc),
                    **counts,
                }
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WEBHOOKS] msg91/%s inbox mark-processed failed: %s", channel, e)

    return {
        "status": "received",
        "vendor": "msg91",
        "channel": channel,
        "webhook_id": webhook_id,
        **counts,
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
                            # Click-to-WhatsApp ads: Meta attaches a referral
                            # object when the chat came from an ad click.
                            "referral": msg.get("referral")
                            if isinstance(msg.get("referral"), dict)
                            else None,
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

            # CTWA ads attribution -- capture only, fail-soft. (The other
            # stamping site is _process_msg91_events for the MSG91 door.)
            if msg.get("referral"):
                try:
                    from ..services.message_events import stamp_ctwa_attribution

                    stamp_ctwa_attribution(phone, msg["referral"])
                except Exception as _ctwa_exc:  # noqa: BLE001
                    logger.debug("[WA_INBOUND] CTWA stamp failed: %s", _ctwa_exc)

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
