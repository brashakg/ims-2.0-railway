"""
IMS 2.0 - Messaging data spine: message_events (ONE collection, ONE writer)
===========================================================================
Every delivery / read / failure / click event about an outbound message, from
every channel MSG91 carries (whatsapp / sms / email / voice / rcs / shorturl),
lands here in ONE shape:

    {
      "event_id":            "<uuid>",
      "channel":             "whatsapp" | "sms" | "email" | "voice" | "rcs"
                             | "shorturl" | "unknown",
      "provider_message_id": "<MSG91 request id>",
      "flow_key":            "<notification_logs.template_id>" | None,
      "store_id":            "<store id>" | None,
      "mobile":              "<last-10-digit identity key>" | None,
      "event":               "delivered" | "read" | "failed" | "clicked",
      "at":                  "<ISO-8601 UTC>",   # provider clock when parseable
      "recorded_at":         "<ISO-8601 UTC>",   # our clock, always
      "raw_status":          "<verbatim provider status>",
      "url":                 "<clicked link>" | None,
    }

THE ONE-EVENTS RULE. record_message_event() below is the ONLY writer of this
collection, and apply_delivery_report() is the ONLY interpreter of an MSG91
delivery report (it advances notification_logs AND records the event, so the
two stores can never drift). The complete enumeration of callers:

  1. api/routers/webhooks.py :: receive_msg91_delivery
     (the pre-existing DLR receiver at POST /api/v1/webhooks/msg91/delivery)
  2. api/routers/webhooks.py :: receive_msg91_channel
     (POST /api/v1/integrations/msg91/webhooks/{channel})

Both call apply_delivery_report(). Nothing else may write message_events or
advance notification_logs.delivery_status past SENT. If you add a third door,
add it to this list and to tests/test_message_events_spine.py's differential
probe.

Identity: `mobile` is the LAST-10-DIGITS key -- the same mobile-primary
convention the customers collection and whatsapp_conversations use.

Dedupe: one row per (channel, provider_message_id, event). MSG91 retries a
webhook 4-5 times on non-2xx; a retry of an already-recorded event is answered
"duplicate" and never double-counted. A unique partial index backstops the
find_one fast path under a multi-worker race.

Fail-soft: nothing in here raises to a caller; a webhook receiver must keep
answering 200 even when this spine cannot record. ASCII only (cp1252).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Channels the spine models. "unknown" is the honest bucket for a delivery
# report we could not attribute to a channel (never guess).
KNOWN_CHANNELS = frozenset(
    {"whatsapp", "sms", "email", "voice", "rcs", "shorturl", "unknown"}
)

# ---------------------------------------------------------------------------
# Status canonicalisation - moved here (VERBATIM) from api/routers/webhooks.py
# so the two receivers cannot drift; the router copy is DELETED, not synced.
# MSG91 reports numeric codes or strings depending on channel; anything not in
# the map is recorded verbatim (upper-cased) so no status is silently eaten.
# ---------------------------------------------------------------------------
MSG91_STATUS_MAP: Dict[str, str] = {
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
    # Short-URL / button click reports.
    "clicked": "CLICKED",
    "click": "CLICKED",
}


def canonical_dlr_status(raw: Any) -> str:
    """MSG91 raw status -> canonical delivery_status (notification_logs)."""
    return MSG91_STATUS_MAP.get(
        str(raw or "").strip().lower(), str(raw or "").upper() or "UNKNOWN"
    )


# Canonical status -> spine event. Deliberately only the four events the spine
# models; SENT/QUEUED/etc. are outbound-side states already on the
# notification_logs row and are NOT events.
_EVENT_FOR_STATUS: Dict[str, str] = {
    "DELIVERED": "delivered",
    "READ": "read",
    "FAILED": "failed",
    "CLICKED": "clicked",
}


def event_for(raw_status: Any) -> Optional[str]:
    """Spine event for a provider status, or None when it is not one of the
    four modelled events (delivered|read|failed|clicked)."""
    return _EVENT_FOR_STATUS.get(canonical_dlr_status(raw_status))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _get_db():
    """Live pymongo Database or None. NEVER truth-test a pymongo object."""
    try:
        from database.connection import get_db as _gd

        conn = _gd()
        if conn is None:
            return None
        database = getattr(conn, "db", None)
        if database is not None:
            return database
        # Test fakes may BE the db-like object.
        if hasattr(conn, "get_collection"):
            return conn
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[MESSAGE_EVENTS] _get_db failed: %s", exc)
        return None


def mobile_key(phone: Any) -> str:
    """Last-10-digit identity key -- the mobile-primary convention shared with
    the customers collection and whatsapp_conversations. '' when unusable."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def _iso_at(raw: Any) -> str:
    """Provider timestamp -> ISO-8601 UTC string; now() when unparseable."""
    now = datetime.now(timezone.utc).isoformat()
    if raw is None:
        return now
    try:
        if isinstance(raw, datetime):
            dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        s = str(raw).strip()
        if not s:
            return now
        if s.isdigit() and len(s) >= 10:
            # Epoch seconds (Meta statuses[] use these) or milliseconds.
            val = int(s)
            if len(s) >= 13:
                val = val // 1000
            return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return now


def _get_events_collection(db=None):
    db = db if db is not None else _get_db()
    if db is None:
        return None
    try:
        coll = db.get_collection("message_events")
        # Unique backstop for the (channel, provider_message_id, event) dedupe
        # under a multi-worker race; PARTIAL so rows with no provider id (none
        # today) would not collide. Fail-soft like webhooks._ensure_index.
        try:
            coll.create_index(
                [("channel", 1), ("provider_message_id", 1), ("event", 1)],
                unique=True,
                partialFilterExpression={"provider_message_id": {"$type": "string"}},
                name="uniq_message_event",
            )
            coll.create_index([("mobile", 1), ("at", -1)], name="mobile_timeline")
            coll.create_index("recorded_at", name="recorded_at")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MESSAGE_EVENTS] index ensure failed: %s", exc)
        return coll
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MESSAGE_EVENTS] collection unavailable: %s", exc)
        return None


def _is_duplicate_key_error(exc: Exception) -> bool:
    if exc.__class__.__name__ == "DuplicateKeyError":
        return True
    try:
        from pymongo.errors import DuplicateKeyError as _DKE

        return isinstance(exc, _DKE)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# THE writer
# ---------------------------------------------------------------------------


def record_message_event(
    *,
    provider_message_id: str,
    event: str,
    channel: Optional[str] = None,
    mobile: Any = None,
    flow_key: Optional[str] = None,
    store_id: Optional[str] = None,
    at: Any = None,
    raw_status: Optional[str] = None,
    url: Optional[str] = None,
    db=None,
) -> str:
    """Record ONE message event. Returns a small honest verdict string:
    "recorded" | "duplicate" | "skipped:<reason>". Never raises.

    Enrichment: when mobile / flow_key / store_id / channel are not supplied
    by the webhook payload, they are resolved from the notification_logs row
    the outbound send stamped (matched by provider_msg_id / provider_id).
    A channel that cannot be resolved is recorded as "unknown" -- honest,
    never guessed.
    """
    pid = str(provider_message_id or "").strip()
    if not pid:
        return "skipped:no_provider_message_id"
    if event not in _EVENT_FOR_STATUS.values():
        return "skipped:not_a_spine_event"

    db = db if db is not None else _get_db()
    if db is None:
        return "skipped:storage_unavailable"

    channel = str(channel or "").strip().lower() or None
    mob = mobile_key(mobile)

    # Enrich from the outbound row this DLR is about.
    if not (mob and flow_key and store_id and channel):
        try:
            log_row = db.get_collection("notification_logs").find_one(
                {"$or": [{"provider_msg_id": pid}, {"provider_id": pid}]}
            )
        except Exception:  # noqa: BLE001
            log_row = None
        if log_row:
            mob = mob or mobile_key(
                log_row.get("customer_phone") or log_row.get("phone")
            )
            flow_key = flow_key or log_row.get("template_id")
            store_id = store_id or log_row.get("store_id")
            channel = channel or str(log_row.get("channel") or "").strip().lower() or None
    if channel not in KNOWN_CHANNELS:
        channel = "unknown"

    coll = _get_events_collection(db)
    if coll is None:
        return "skipped:storage_unavailable"

    # Fast-path dedupe; the unique partial index is the race backstop.
    try:
        if coll.find_one(
            {"channel": channel, "provider_message_id": pid, "event": event}
        ):
            return "duplicate"
    except Exception:  # noqa: BLE001
        pass

    doc = {
        "event_id": str(uuid.uuid4()),
        "channel": channel,
        "provider_message_id": pid,
        "flow_key": flow_key or None,
        "store_id": store_id or None,
        "mobile": mob or None,
        "event": event,
        "at": _iso_at(at),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "raw_status": str(raw_status) if raw_status is not None else None,
        "url": str(url)[:2048] if url else None,
    }
    try:
        coll.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        if _is_duplicate_key_error(exc):
            return "duplicate"
        logger.error("[MESSAGE_EVENTS] insert failed: %s", exc)
        return "skipped:insert_failed"
    return "recorded"


# ---------------------------------------------------------------------------
# THE delivery-report interpreter (both webhook receivers call this)
# ---------------------------------------------------------------------------


def apply_delivery_report(
    *,
    provider_message_id: str,
    raw_status: Any,
    channel: Optional[str] = None,
    mobile: Any = None,
    at: Any = None,
    url: Optional[str] = None,
    db=None,
) -> Dict[str, Any]:
    """ONE rule for what an MSG91 delivery report does:

      1. Advance delivery_status on the matching notification_logs row(s)
         (moved VERBATIM from the receiver: same fields, same DELIVERED ->
         delivered_at stamp, same update_many match on provider_msg_id /
         provider_id, same fail-soft).
      2. Record the spine event when the status maps to one
         (delivered|read|failed|clicked); SENT-class statuses only advance
         the log row.

    Returns {"delivery_status", "logs_updated", "event", "event_result"}.
    Never raises.
    """
    pid = str(provider_message_id or "").strip()
    canonical = canonical_dlr_status(raw_status)
    out: Dict[str, Any] = {
        "delivery_status": canonical,
        "logs_updated": 0,
        "event": None,
        "event_result": None,
    }
    if not pid:
        return out

    db = db if db is not None else _get_db()
    if db is not None:
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
                {"$or": [{"provider_msg_id": pid}, {"provider_id": pid}]},
                {"$set": update},
            )
            out["logs_updated"] = getattr(res, "modified_count", 0) or 0
        except Exception as exc:  # noqa: BLE001
            # Stay green so MSG91 doesn't retry-storm; the miss is logged.
            logger.error("[MESSAGE_EVENTS] DLR log update failed: %s", exc)

    ev = event_for(raw_status)
    if ev:
        out["event"] = ev
        out["event_result"] = record_message_event(
            provider_message_id=pid,
            event=ev,
            channel=channel,
            mobile=mobile,
            at=at,
            raw_status=str(raw_status),
            url=url,
            db=db,
        )
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def customer_message_timeline(
    phone: Any, limit: int = 50, db=None
) -> List[Dict[str, Any]]:
    """Most-recent-first message events for one customer (mobile-primary
    match). Fail-soft -> []."""
    key = mobile_key(phone)
    if not key:
        return []
    coll = _get_events_collection(db)
    if coll is None:
        return []
    try:
        out = []
        for doc in coll.find({"mobile": key}).sort("at", -1).limit(int(limit)):
            doc.pop("_id", None)
            out.append(doc)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("[MESSAGE_EVENTS] timeline read failed: %s", exc)
        return []


def failure_counts(days: int = 7, db=None) -> Optional[Dict[str, int]]:
    """{"failed": n, "total": n} over a rolling window on recorded_at (our
    clock -- provider clocks are unreliable). None when storage unreadable."""
    coll = _get_events_collection(db)
    if coll is None:
        return None
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=int(days))
    ).isoformat()
    try:
        return {
            "failed": coll.count_documents(
                {"event": "failed", "recorded_at": {"$gte": cutoff}}
            ),
            "total": coll.count_documents({"recorded_at": {"$gte": cutoff}}),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("[MESSAGE_EVENTS] failure_counts failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Click-to-WhatsApp ads attribution (capture only -- no screens)
# ---------------------------------------------------------------------------

# The referral keys Meta documents on a CTWA inbound message. Only these are
# captured (webhook bodies are untrusted input; never store arbitrary blobs).
_REFERRAL_KEYS = (
    "source_url",
    "source_id",
    "source_type",
    "headline",
    "body",
    "media_type",
    "ctwa_clid",
)


def stamp_ctwa_attribution(phone: Any, referral: Any, db=None) -> bool:
    """Stamp Click-to-WhatsApp ad attribution onto the customer record
    (mobile-primary match). First touch is written once and never
    overwritten; last touch always updates. Returns True when a customer row
    was stamped. Fail-soft -> False. Callers (the complete enumeration):

      1. webhooks.receive_whatsapp_inbound   (Meta direct inbound)
      2. webhooks._process_msg91_events      (MSG91 whatsapp channel inbound)
    """
    if not isinstance(referral, dict) or not referral:
        return False
    key = mobile_key(phone)
    if not key:
        return False
    db = db if db is not None else _get_db()
    if db is None:
        return False
    try:
        stamp = {
            k: str(referral[k])[:300]
            for k in _REFERRAL_KEYS
            if referral.get(k) is not None
        }
        if not stamp:
            return False
        stamp["source"] = "ctwa"
        stamp["at"] = datetime.now(timezone.utc).isoformat()

        coll = db.get_collection("customers")
        cust = coll.find_one({"mobile": key})
        if not cust:
            return False
        ident = {"customer_id": cust["customer_id"]} if cust.get(
            "customer_id"
        ) else {"mobile": key}
        # First touch: only when absent (the ad that ACQUIRED the chat).
        coll.update_one(
            {**ident, "ads_attribution.first": {"$exists": False}},
            {"$set": {"ads_attribution.first": dict(stamp)}},
        )
        # Last touch: always.
        coll.update_one(
            ident,
            {
                "$set": {"ads_attribution.last": dict(stamp)},
                "$inc": {"ads_attribution.touches": 1},
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MESSAGE_EVENTS] CTWA stamp failed: %s", exc)
        return False
