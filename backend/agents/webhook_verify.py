"""
IMS 2.0 — Inbound webhook signature verifiers (Phase I-2)
============================================================
Pure HMAC verifiers for the three vendors that push webhooks to IMS:

- Razorpay  — payment lifecycle (payment.captured, refund.created, etc.)
- Shopify   — order/product mutations from the storefront
- Shiprocket — shipment status transitions

Design contract:
- These are PURE FUNCTIONS. No I/O, no env reads, no Mongo, no logging
  side-effects. The caller passes in the secret it loaded from the
  `integrations` collection. This makes the functions trivially unit-
  testable: feed in known body+secret+signature, assert True/False.
- Comparisons use `hmac.compare_digest` so we don't leak timing info on
  a partial-prefix match.
- Any exception (malformed header, garbage secret, encoding error) is
  swallowed and we return False. The receiver never receives a 500 from
  the verifier itself; bad input is "invalid signature".
- The only env-aware function is `is_replay`, which reads
  `WEBHOOK_REPLAY_WINDOW_SECONDS` (default 300) at call time. It's
  separate so the verifiers themselves stay fully pure.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Optional


# ============================================================================
# Razorpay — hex HMAC-SHA256
# ============================================================================


def verify_razorpay(body: bytes, signature_header: str, secret: str) -> bool:
    """
    Razorpay sends `X-Razorpay-Signature: <hex hmac-sha256 of raw body>`.

    Doc: https://razorpay.com/docs/webhooks/validate-test/
    """
    if not body or not signature_header or not secret:
        return False
    if not isinstance(body, (bytes, bytearray)):
        return False
    try:
        expected = hmac.new(
            secret.encode("utf-8"),
            bytes(body),
            hashlib.sha256,
        ).hexdigest()
        # signature_header is hex too
        return hmac.compare_digest(expected, signature_header.strip())
    except (TypeError, ValueError, AttributeError):
        return False


# ============================================================================
# Shopify — base64 HMAC-SHA256
# ============================================================================


def verify_shopify(body: bytes, hmac_header: str, secret: str) -> bool:
    """
    Shopify sends `X-Shopify-Hmac-Sha256: <base64 hmac-sha256 of raw body>`
    using the API/webhook secret.

    Doc: https://shopify.dev/docs/apps/build/webhooks/subscribe/https
    """
    if not body or not hmac_header or not secret:
        return False
    if not isinstance(body, (bytes, bytearray)):
        return False
    try:
        digest = hmac.new(
            secret.encode("utf-8"),
            bytes(body),
            hashlib.sha256,
        ).digest()
        expected_b64 = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected_b64, hmac_header.strip())
    except (TypeError, ValueError, AttributeError, binascii.Error):
        return False


# ============================================================================
# Shiprocket — hex HMAC-SHA256
# ============================================================================


def verify_shiprocket(body: bytes, signature_header: str, secret: str) -> bool:
    """
    Shiprocket's webhook docs are sparse. Industry standard for an HTTP
    webhook with a shared secret is hex HMAC-SHA256 — same shape as
    Razorpay. If/when Shiprocket publishes a more specific scheme we
    swap the implementation here without touching the receiver.

    Header: `X-Shiprocket-Signature`.
    """
    if not body or not signature_header or not secret:
        return False
    if not isinstance(body, (bytes, bytearray)):
        return False
    try:
        expected = hmac.new(
            secret.encode("utf-8"),
            bytes(body),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header.strip())
    except (TypeError, ValueError, AttributeError):
        return False


# ============================================================================
# MSG91 - hex HMAC-SHA256 (delivery-report / DLR webhook)
# ============================================================================


def verify_msg91(body: bytes, signature_header: str, secret: str) -> bool:
    """
    MSG91 posts delivery reports (DLRs) to a configured webhook URL. MSG91's
    own scheme is a shared-secret HMAC; the industry-standard shape (and what we
    require here) is hex HMAC-SHA256 over the raw body -- identical to Razorpay
    / Shiprocket. The shared secret lives in the `integrations` doc
    (type "msg91", key `webhook_secret`). If MSG91 publishes a more specific
    header scheme we swap the implementation here without touching the receiver.

    Header: `X-MSG91-Signature`.
    """
    if not body or not signature_header or not secret:
        return False
    if not isinstance(body, (bytes, bytearray)):
        return False
    try:
        expected = hmac.new(
            secret.encode("utf-8"),
            bytes(body),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header.strip())
    except (TypeError, ValueError, AttributeError):
        return False


# ============================================================================
# Replay protection
# ============================================================================


def _replay_window_seconds() -> int:
    """Read replay window from env at call time (so tests can monkeypatch)."""
    raw = os.getenv("WEBHOOK_REPLAY_WINDOW_SECONDS", "300")
    try:
        n = int(raw)
        return n if n > 0 else 300
    except (TypeError, ValueError):
        return 300


def _parse_iso(timestamp_str: str) -> Optional[datetime]:
    """Tolerant ISO-8601 parse. Accepts trailing Z. Returns None on garbage."""
    if not timestamp_str:
        return None
    try:
        s = str(timestamp_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        # Treat naive timestamps as UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def is_replay(timestamp_str: str, window_seconds: Optional[int] = None) -> bool:
    """
    Best-effort replay detector. Returns True when the supplied timestamp
    is older than `window_seconds` (default 300 / 5 minutes) from now.

    Designed to be called AFTER signature verification — its job is to
    reject correctly-signed but stale envelopes that an attacker might
    capture-and-resend. A False result simply means "not too old to
    process"; receivers can decide what to do with True.

    Tolerant of:
      - missing / empty timestamp (returns False — caller may choose to
        log "no timestamp, can't replay-check")
      - garbage timestamp (returns False on parse fail)
      - naive timestamps (assumed UTC)
    """
    if not timestamp_str:
        return False
    window = window_seconds if (window_seconds and window_seconds > 0) else _replay_window_seconds()
    parsed = _parse_iso(timestamp_str)
    if parsed is None:
        return False
    now = datetime.now(timezone.utc)
    age = (now - parsed).total_seconds()
    return age > window


# Public surface
__all__ = [
    "verify_razorpay",
    "verify_shopify",
    "verify_shiprocket",
    "verify_msg91",
    "is_replay",
]
