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
- The env-aware functions are `is_replay` / `is_stale_delivery`, which read
  `WEBHOOK_REPLAY_WINDOW_SECONDS` / `WEBHOOK_DELIVERY_MAX_AGE_SECONDS` at
  call time. They're separate so the verifiers themselves stay fully pure.

Replay protection lives at the bottom of this module. Read the block
comment there before changing anything in it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

# Used ONLY by the env-reading helpers at the bottom of this module (to warn
# about a misconfigured cap). The signature verifiers stay side-effect free.
logger = logging.getLogger(__name__)


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
#
# WHY THIS SECTION LOOKS THE WAY IT DOES (P1 fix, 2026-08-09)
# ------------------------------------------------------------------------
# The receiver used to compute its freshness check from the *business
# object's* timestamp -- `payload["created_at"]`, which for a Shopify order
# is WHEN THE CUSTOMER PLACED THE ORDER. Paired with a 300 s window that
# meant:
#   * every later-lifecycle webhook about an older order (payment update,
#     fulfillment, cancellation, refund) was classified "replay" and
#     silently dropped -- losing order-status changes and GST-bearing
#     events;
#   * every vendor RETRY of a transient failure was dropped the same way
#     (Shopify retries with backoff for ~48 h, Razorpay for ~24 h);
#   * and it did NOT stop the attack it existed to stop -- replaying a
#     freshly captured new-order webhook sails through, because a new
#     order's created_at is by definition fresh.
#
# So the freshness check MUST be keyed on the DELIVERY's own clock, never
# on a business field. Two consequences shape the API below:
#
#   1. Delivery timestamps arrive in vendor HEADERS (Shopify:
#      X-Shopify-Triggered-At), and headers sit OUTSIDE the HMAC -- an
#      attacker replaying a captured body can rewrite them freely, or
#      simply OMIT them (no header -> no timestamp -> no cap at all).
#      A header-derived window is therefore a STALENESS CAP ONLY, never
#      the load-bearing control, and its window must be wider than every
#      vendor's retry horizon so it can never eat a legitimate retry.
#      Razorpay is the exception: its event-emission time rides INSIDE the
#      signed envelope (top-level integer `created_at` epoch), so for
#      Razorpay -- and ONLY Razorpay -- the cap is signature-bound and
#      cannot be stripped.
#
#      Do NOT read the "cap <= dedupe retention" alignment below as a
#      universal age bound. It is signature-bound for Razorpay only. For
#      Shopify and Shiprocket there is NO enforceable age bound and dedupe
#      carries 100% of the replay defence. That is the correct fail-safe
#      direction (a missing clock accepts rather than drops), but the next
#      reader must not lean on the invariant.
#
#   2. The load-bearing anti-replay is CONTENT-BOUND dedupe.
#      `body_fingerprint()` hashes the exact bytes the HMAC covers, so an
#      attacker cannot change one byte of the BODY without invalidating the
#      signature. The receiver stores the digest behind a unique index, so a
#      replay of the same signed bytes is rejected regardless of age.
#
#      BE PRECISE ABOUT WHAT IS AND IS NOT SIGNED. The fingerprint also mixes
#      in a `scope` -- the vendor's event-type header -- so two genuinely
#      different topics carrying a byte-identical body are not collapsed into
#      one delivery. That header is NOT covered by the HMAC. It is
#      attacker-mutable, and downstream it is also routing-bearing (NEXUS
#      selects the money handler from it). Feeding it in raw therefore let ONE
#      signed body mint an UNBOUNDED number of dedupe keys: round 1 of this
#      fix failed on the casing axis (5 spellings -> 5 keys), and normalising
#      case alone still left the VALUE axis open (5000 random topic strings ->
#      5000 keys).
#
#      So the scope is now mapped through a CLOSED ALLOWLIST
#      (`canonical_scope`) before it is hashed: recognised vendor topics keep
#      their identity, whole edit-only families collapse to one bucket each,
#      and everything else collapses to a single `unknown` bucket. The honest
#      invariant is therefore:
#
#          the fingerprint is content-bound over the SIGNED bytes, and the
#          number of distinct dedupe keys one signed body can produce is
#          bounded by the size of that closed set -- not by what an attacker
#          can put in a header.
#
# Do not "simplify" this back into a single narrow window over a payload
# field. That is the bug. And do not pass an unvalidated header into
# body_fingerprint; that is the other bug.
# ============================================================================


# ============================================================================
# Closed allowlist for the fingerprint scope
# ============================================================================
# Mirrors the topic sets NEXUS actually dispatches on
# (`NexusAgent._SHOPIFY_ORDER_TOPICS`, `._SHOPIFY_TOPIC_HANDLERS`,
# `._SHOPIFY_EDIT_ONLY_PREFIXES`). It is deliberately a COPY rather than an
# import: this module is a pure leaf with no agent dependencies, and the
# receiver must not gain an import edge into the agent layer. The copy is
# kept honest by a drift tripwire in tests/test_webhook_replay_window.py,
# which imports NEXUS's real constants and fails if either side gains a topic
# the other does not know about.
#
# Vendors absent from this table (Razorpay, Shiprocket) have no published
# topic vocabulary, so EVERY scope value they send collapses to the single
# unknown bucket. That is the safe direction: a closed set of one.
# ============================================================================

SHOPIFY_TOPIC_ALLOWLIST = frozenset(
    {
        # order lifecycle -> NEXUS _SHOPIFY_ORDER_TOPICS
        "orders/create",
        "orders/paid",
        "orders/updated",
        "orders/cancelled",
        "orders/fulfilled",
        "orders/partially_fulfilled",
        # non-order topics IMS owns -> NEXUS _SHOPIFY_TOPIC_HANDLERS
        "refunds/create",
        "fulfillments/create",
        "fulfillments/update",
        "customers/create",
        "customers/update",
        "orders/delete",
        "customers/delete",
        "app/uninstalled",
        "checkouts/create",
        "checkouts/update",
    }
)

# Edit-only-in-IMS families -> NEXUS _SHOPIFY_EDIT_ONLY_PREFIXES. Every member
# of a family shares ONE bucket: IMS never pulls these back, so there is no
# per-topic behaviour to preserve, and one bucket per family keeps the key
# space closed no matter what suffix arrives.
SHOPIFY_TOPIC_FAMILIES = (
    "products/",
    "collections/",
    "inventory_levels/",
    "inventory_items/",
    "product_listings/",
)

# Every unrecognised scope, for every vendor, lands here.
UNKNOWN_SCOPE = "unknown"

_SCOPE_ALLOWLISTS = {
    "shopify": (SHOPIFY_TOPIC_ALLOWLIST, SHOPIFY_TOPIC_FAMILIES),
}


def canonical_scope(vendor: str, raw_scope: Optional[str]) -> str:
    """Map a vendor event-type header onto a CLOSED set of scope values.

    The raw header is unsigned and attacker-chosen, so it must never reach
    `body_fingerprint` directly -- see the block comment above. Returns:

      - "" when the vendor sends no event-type header at all;
      - the normalised topic when it is a recognised vendor topic;
      - "<family>*" when it belongs to a recognised edit-only family;
      - UNKNOWN_SCOPE for everything else, including every scope value from
        a vendor with no published topic vocabulary.

    Pure and total: any input, including None or a 10 KB junk string, yields
    one of the values above.
    """
    normalised = (raw_scope or "").strip().lower()
    if not normalised:
        return ""
    exact, families = _SCOPE_ALLOWLISTS.get((vendor or "").strip().lower(), (None, ()))
    if exact and normalised in exact:
        return normalised
    for family in families:
        if normalised.startswith(family):
            return family + "*"
    return UNKNOWN_SCOPE


# Vendor -> header carrying the DELIVERY/trigger timestamp (lower-cased).
# Only headers the vendor actually documents belong here; guessing a header
# name would silently disable the staleness cap.
DELIVERY_TIMESTAMP_HEADERS = {
    "shopify": ("x-shopify-triggered-at",),
    # Razorpay sends no delivery-time header; its event time is in the signed
    # envelope instead (handled in extract_delivery_timestamp).
    "razorpay": (),
    # Shiprocket documents neither a delivery id nor a delivery timestamp;
    # it relies entirely on body-fingerprint dedupe.
    "shiprocket": (),
}

# How long the receiver's dedupe store keeps a delivery. This is the TTL on
# webhook_inbox.received_at -- api/routers/webhooks.py imports THIS constant
# for its TTL index so the two numbers can never drift apart.
DEDUPE_RETENTION_SECONDS = 30 * 24 * 3600

# Staleness cap default == the dedupe retention above, deliberately.
#
# The cap exists ONLY to cover the window where dedupe genuinely cannot help:
# a delivery older than the retention has had its dedupe row expire, so it
# would be re-ingestible. Anything younger is already rejected by
# fingerprint/id dedupe, so a tighter cap would add no protection and only
# create a way to drop real deliveries. It was 7 days in the first cut of
# this fix; that bought nothing and gave Razorpay -- whose clock is inside
# the HMAC and therefore cannot be omitted by an operator resending from the
# dashboard -- a new silent-drop path after a >7-day outage.
_DELIVERY_MAX_AGE_DEFAULT = DEDUPE_RETENTION_SECONDS

# Hard floor for the env override. The whole point of this module's fix is
# that a narrow freshness window destroys legitimate lifecycle traffic and
# vendor retries; an operator pasting the old WEBHOOK_REPLAY_WINDOW_SECONDS
# value (300) into the new key would reinstate that outage on live GST
# traffic. 48 h is the longest vendor retry horizon (Shopify), so no
# configured value may sit below it.
_DELIVERY_MAX_AGE_FLOOR = 48 * 3600


def _replay_window_seconds() -> int:
    """Read replay window from env at call time (so tests can monkeypatch)."""
    raw = os.getenv("WEBHOOK_REPLAY_WINDOW_SECONDS", "300")
    try:
        n = int(raw)
        return n if n > 0 else 300
    except (TypeError, ValueError):
        return 300


def delivery_max_age_seconds() -> int:
    """Staleness cap for a webhook DELIVERY timestamp, read at call time.

    Env: `WEBHOOK_DELIVERY_MAX_AGE_SECONDS` (default = DEDUPE_RETENTION_SECONDS,
    2592000 = 30 days). Garbage / non-positive values fall back to the default
    rather than producing a zero-width window that would reject everything,
    and any value below `_DELIVERY_MAX_AGE_FLOOR` (48 h, the longest vendor
    retry horizon) is raised to the floor with a warning -- a too-small cap
    is the original outage, not a tightening.

    Callers that need an exact window for testing should pass
    `window_seconds=` to `is_stale_delivery` instead; that argument is
    honoured verbatim.
    """
    raw = os.getenv("WEBHOOK_DELIVERY_MAX_AGE_SECONDS", "")
    if not raw:
        return _DELIVERY_MAX_AGE_DEFAULT
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DELIVERY_MAX_AGE_DEFAULT
    if n <= 0:
        return _DELIVERY_MAX_AGE_DEFAULT
    if n < _DELIVERY_MAX_AGE_FLOOR:
        logger.warning(
            "[WEBHOOK_VERIFY] WEBHOOK_DELIVERY_MAX_AGE_SECONDS=%s is below the "
            "%ss floor (the longest vendor retry horizon); using the floor. A "
            "cap this small drops legitimate lifecycle events and retries.",
            n,
            _DELIVERY_MAX_AGE_FLOOR,
        )
        return _DELIVERY_MAX_AGE_FLOOR
    return n


def _parse_iso(timestamp_str: str) -> Optional[datetime]:
    """Tolerant timestamp parse. Accepts ISO-8601 (incl. trailing Z) and
    unix epoch in seconds (10 digits) or milliseconds (13 digits), as an int
    or a numeric string. Returns None on garbage.

    Epoch support exists because Razorpay stamps its signed envelope with an
    integer `created_at`; without it that timestamp parsed as garbage and the
    staleness cap silently did nothing for Razorpay.
    """
    if timestamp_str is None or timestamp_str == "":
        return None
    try:
        if isinstance(timestamp_str, bool):
            return None
        if isinstance(timestamp_str, (int, float)):
            return _parse_epoch_number(float(timestamp_str))
        s = str(timestamp_str).strip()
        if not s:
            return None
        # Unix epoch: only unambiguous widths (10 = seconds, 13 = millis).
        # Narrower all-digit strings such as "20260809" are ISO basic dates
        # and must keep going through fromisoformat.
        if s.isdigit() and len(s) in (10, 13):
            return _parse_epoch_number(float(s))
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        s = _normalise_fractional_seconds(s)
        dt = datetime.fromisoformat(s)
        # Treat naive timestamps as UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# Matches the fractional-seconds group in an ISO-8601 timestamp, e.g. the
# ".320072772" in "2026-08-09T10:15:16.320072772+00:00".
# ANCHORED to the seconds field: an unanchored r"\.(\d+)" matched the first
# dot-digits group anywhere, so a dotted date like "2026.08.09" was mangled
# into "2026.080000.09". Fail-safe today (both forms fail fromisoformat, and
# a None parse means ACCEPT) but wrong, and a future caller feeding a dotted
# date would inherit the bug.
_FRACTION_RE = re.compile(r"(?<=\d{2}:\d{2}:\d{2})\.(\d+)")


def _normalise_fractional_seconds(s: str) -> str:
    """Pad/truncate ISO fractional seconds to exactly 6 digits.

    Shopify's X-Shopify-Triggered-At carries NINE fractional digits
    ("2026-08-09T10:15:16.320072772Z"). `datetime.fromisoformat` before
    Python 3.11 accepts only 3 or 6 digits, and 3.10 is a REQUIRED CI target
    here -- so without this the parse fails, `_parse_iso` returns None, and
    the staleness cap becomes a silent no-op for the vendor it matters most
    for. Fail-safe either way (None means "accept"), but a control that
    quietly does nothing on half the deploy matrix is not a control.
    """
    m = _FRACTION_RE.search(s)
    if not m:
        return s
    digits = m.group(1)
    if len(digits) == 6:
        return s
    fixed = (digits + "000000")[:6]
    return s[: m.start()] + "." + fixed + s[m.end():]


def _parse_epoch_number(value: float) -> Optional[datetime]:
    """Epoch seconds or milliseconds -> aware UTC datetime. None on garbage."""
    try:
        # >= 1e11 seconds would be the year 5138; treat that magnitude as ms.
        seconds = value / 1000.0 if abs(value) >= 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _lower_headers(headers: Optional[Mapping[str, str]]) -> dict:
    """Lower-cased copy of a header mapping. Never raises."""
    out: dict = {}
    if not headers:
        return out
    try:
        items = headers.items()
    except AttributeError:
        return out
    for k, v in items:
        try:
            out[str(k).lower()] = v
        except Exception:  # noqa: BLE001
            continue
    return out


def extract_delivery_timestamp(
    vendor: str,
    headers: Optional[Mapping[str, str]] = None,
    payload: Optional[Any] = None,
) -> Optional[str]:
    """Return the DELIVERY's own timestamp for `vendor`, or None.

    NEVER returns a business-object timestamp. In particular it does not
    look at a Shopify order's `created_at` / `updated_at`, which is what the
    original replay check used and why every lifecycle event was dropped.

    Sources, in order:
      - the vendor's documented delivery/trigger header (Shopify:
        `X-Shopify-Triggered-At`);
      - Razorpay only: the top-level integer `created_at` on the *event
        envelope*. That field is the event-emission time and it is inside
        the HMAC-signed body, so it cannot be forged. The payment's own
        created_at lives further down at payload.payment.entity.created_at
        and is deliberately NOT consulted.

    Returns None when the vendor sends no delivery clock at all
    (Shiprocket, or an older Shopify payload without the header). None means
    "no staleness cap available" -- the caller must fall through to
    fingerprint dedupe and accept the delivery, never drop it.
    """
    v = (vendor or "").lower()
    hdrs = _lower_headers(headers)
    for name in DELIVERY_TIMESTAMP_HEADERS.get(v, ()):
        value = hdrs.get(name)
        if value:
            return str(value)

    if v == "razorpay" and isinstance(payload, dict):
        # Only accept a numeric epoch. A Razorpay event envelope always
        # carries `created_at` as an integer; anything else is not the
        # envelope we think it is, and we refuse to guess.
        created = payload.get("created_at")
        if isinstance(created, bool):
            return None
        if isinstance(created, (int, float)):
            return str(int(created))
        if isinstance(created, str) and created.strip().isdigit():
            return created.strip()
    return None


def body_fingerprint(vendor: str, raw_body: bytes, scope: str = "") -> str:
    """Content-bound dedupe key over the EXACT bytes the HMAC signed.

    This is the load-bearing anti-replay control: a replayed delivery is by
    definition the same signed bytes, so it produces the same fingerprint
    and the receiver's unique index rejects it -- for as long as the dedupe
    store retains the row, and independently of any attacker-mutable header.

    `scope` should be the vendor's event-TYPE discriminator (Shopify
    `X-Shopify-Topic`, Shiprocket `X-Shiprocket-Event`) so that two
    genuinely different topics that happen to carry a byte-identical body
    are not collapsed into one. Vendor is always mixed in so two vendors
    can never collide.

    THE SCOPE IS UNSIGNED. It comes from a vendor header that the HMAC does
    not cover, so callers MUST pass it through `canonical_scope` first;
    `body_fingerprint` additionally strips + lower-cases both arguments so a
    caller that forgets cannot at least reopen the casing axis. Two rounds of
    review were spent on this exact surface:

      round 1 -- the scope was hashed verbatim, so 5 spellings of one topic
        made 5 dedupe keys for identical signed bytes;
      round 2 -- case was normalised but the VALUE was still unvalidated, so
        5000 random topic strings made 5000 keys. Normalisation is necessary
        and not sufficient; only the closed allowlist bounds the key space.

    The honest guarantee: the digest is content-bound over the signed bytes,
    and because `scope` is drawn from a closed set, the number of distinct
    dedupe keys ONE signed body can produce is bounded by the number of real
    vendor topics -- not by anything an attacker controls. Distinct topics
    stay distinct (orders/paid vs orders/updated are separate deliveries);
    only spellings and unrecognised junk collapse.

    Returns "sha256:<hex>" (the prefix keeps synthetic keys visually
    distinct from vendor-issued delivery ids in the inbox).
    """
    h = hashlib.sha256()
    h.update(
        f"{(vendor or '').strip().lower()}\n{(scope or '').strip().lower()}\n".encode(
            "utf-8"
        )
    )
    if isinstance(raw_body, (bytes, bytearray)):
        h.update(bytes(raw_body))
    elif raw_body:
        h.update(str(raw_body).encode("utf-8", errors="replace"))
    return "sha256:" + h.hexdigest()


def _is_older_than(timestamp_str: str, window: int) -> bool:
    parsed = _parse_iso(timestamp_str)
    if parsed is None:
        return False
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    return age > window


def is_stale_delivery(
    timestamp_str: str, window_seconds: Optional[int] = None
) -> bool:
    """True when a webhook DELIVERY timestamp is older than the staleness cap.

    Call this with the value from `extract_delivery_timestamp` -- i.e. the
    delivery's own clock. Never call it with a business object's created_at
    (that was the bug this module now guards against).

    Fail-safe: an empty, missing, or unparseable timestamp returns False
    ("not stale"), so a delivery is never dropped just because we could not
    read a clock. Genuine replay cover comes from fingerprint/id dedupe.
    """
    if not timestamp_str:
        return False
    window = (
        window_seconds
        if (window_seconds and window_seconds > 0)
        else delivery_max_age_seconds()
    )
    return _is_older_than(timestamp_str, window)


def is_replay(timestamp_str: str, window_seconds: Optional[int] = None) -> bool:
    """
    DEPRECATED for receiver use -- kept as a pure predicate + for callers
    that genuinely have a delivery clock and want the narrow (300 s) window.

    Returns True when the supplied timestamp is older than `window_seconds`
    (default `WEBHOOK_REPLAY_WINDOW_SECONDS`, 300) from now.

    DO NOT feed this a business object's timestamp. Passing a Shopify
    order's `created_at` here is what made every payment/fulfillment/
    cancellation/refund webhook about an order older than 5 minutes look
    like a replay and get silently dropped. Receivers must use
    `extract_delivery_timestamp` + `is_stale_delivery` instead.

    Tolerant of:
      - missing / empty timestamp (returns False)
      - garbage timestamp (returns False on parse fail)
      - naive timestamps (assumed UTC)
    """
    if not timestamp_str:
        return False
    window = window_seconds if (window_seconds and window_seconds > 0) else _replay_window_seconds()
    return _is_older_than(timestamp_str, window)


# Public surface
__all__ = [
    "verify_razorpay",
    "verify_shopify",
    "verify_shiprocket",
    "verify_msg91",
    "is_replay",
    "is_stale_delivery",
    "extract_delivery_timestamp",
    "body_fingerprint",
    "delivery_max_age_seconds",
    "DELIVERY_TIMESTAMP_HEADERS",
    "DEDUPE_RETENTION_SECONDS",
    "canonical_scope",
    "SHOPIFY_TOPIC_ALLOWLIST",
    "SHOPIFY_TOPIC_FAMILIES",
    "UNKNOWN_SCOPE",
]
