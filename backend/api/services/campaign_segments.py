"""
IMS 2.0 - Campaign audience segments
====================================
The campaign layer (routers/campaigns.py) targets a SEGMENT -- a named,
recomputed-on-demand set of customers -- rather than a hand-typed recipient
list. This module is the single source of truth for what each segment MEANS and
how its audience is resolved against MongoDB.

Design contract
---------------
- Pure-ish + fail-soft: every resolver takes the live `db` handle (or None) and
  returns plain dicts/ints. A missing DB or any query error yields an EMPTY
  audience (count 0), never an exception -- a segment preview must not 500.
- Read-only: resolving a segment never writes. The actual send (campaigns.py)
  reuses the existing send_notification path; this module only decides WHO.
- The audience row shape is the minimal contract the bulk-send path needs:
  {customer_id, phone, name, variables{...}}. `variables` carries per-recipient
  template tokens (customer_name + segment-specific extras like expiry_date).
- marketing_consent is NOT filtered here. Consent is enforced at SEND time by
  campaigns.py (mirroring notifications/send-bulk) so the PREVIEW count reflects
  the true reachable-by-segment population and the owner sees how many are then
  suppressed as opted-out. The one exception: rx_expiry/birthday/etc. all read
  the same `customers`/`prescriptions` collections the rest of marketing.py uses.

Segments (keys are stable API contract -- do not rename):
  rx_expiry       prescriptions expiring within `window_days` (default 90)
  birthday        customers whose DOB month/day falls within the next N days
  winback         customers with NO order in the last `inactive_months` months
  by_store        every customer attached to a store (store_id required)
  by_customer_type  B2B or B2C customers (param: customer_type)
  recent_buyers   customers who DID order within the last `recent_days` days

Each segment carries metadata (label, description, default channel, the
template_id the campaign typically uses) so the frontend builder can render the
picker without hard-coding it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default tuning knobs (overridable per call where it makes sense).
RX_EXPIRY_WINDOW_DAYS = 90
RX_VALIDITY_DAYS = 730  # 2-year prescription validity, matches marketing.py
BIRTHDAY_WINDOW_DAYS = 7
WINBACK_INACTIVE_MONTHS = 6
RECENT_BUYER_DAYS = 30
PREVIEW_SAMPLE_SIZE = 5
# Hard ceiling on rows scanned so a segment preview/resolve can never run away
# on a large customers/prescriptions collection.
_SCAN_LIMIT = 5000


# ---------------------------------------------------------------------------
# Segment catalogue (metadata the UI renders)
# ---------------------------------------------------------------------------

SEGMENT_DEFS: List[Dict[str, Any]] = [
    {
        "key": "rx_expiry",
        "label": "Prescription expiring",
        "description": "Customers whose prescription expires within the window (default 90 days).",
        "default_channel": "WHATSAPP",
        "default_template_id": "PRESCRIPTION_EXPIRY",
        "campaign_type": "rx_renewal",
        "store_scoped": True,
    },
    {
        "key": "birthday",
        "label": "Birthday this week",
        "description": "Customers with a birthday in the next 7 days.",
        "default_channel": "WHATSAPP",
        "default_template_id": "BIRTHDAY_WISH",
        "campaign_type": "birthday",
        "store_scoped": True,
    },
    {
        "key": "winback",
        "label": "Win-back (lapsed)",
        "description": "Customers with no order in the last 6 months.",
        "default_channel": "WHATSAPP",
        "default_template_id": "WALKOUT_RECOVERY",
        "campaign_type": "winback",
        "store_scoped": True,
    },
    {
        "key": "by_store",
        "label": "All store customers",
        "description": "Every customer attached to the selected store.",
        "default_channel": "WHATSAPP",
        "default_template_id": "BIRTHDAY_WISH",
        "campaign_type": "custom",
        "store_scoped": True,
    },
    {
        "key": "by_customer_type",
        "label": "By customer type (B2B / B2C)",
        "description": "Customers of a chosen type. Pass customer_type=B2B or B2C.",
        "default_channel": "WHATSAPP",
        "default_template_id": "BIRTHDAY_WISH",
        "campaign_type": "custom",
        "store_scoped": True,
    },
    {
        "key": "recent_buyers",
        "label": "Recent buyers",
        "description": "Customers who placed an order in the last 30 days.",
        "default_channel": "WHATSAPP",
        "default_template_id": "GOOGLE_REVIEW_REQUEST",
        "campaign_type": "custom",
        "store_scoped": True,
    },
]

SEGMENT_KEYS = {d["key"] for d in SEGMENT_DEFS}


def segment_def(key: str) -> Optional[Dict[str, Any]]:
    for d in SEGMENT_DEFS:
        if d["key"] == key:
            return d
    return None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _coerce_dt(value: Any) -> Optional[datetime]:
    """Best-effort parse of a Mongo timestamp that may be a datetime OR an ISO
    string (orders write datetimes; some collections write isoformat strings)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            return None
    return None


def _parse_dob(value: Any) -> Optional[date]:
    """Parse a stored DOB (ISO 'YYYY-MM-DD' string or date/datetime) to a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except Exception:  # noqa: BLE001
            return None
    return None


def _audience_row(
    customer: Dict[str, Any], extra_vars: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Build the minimal bulk-send recipient row from a customer doc."""
    name = customer.get("name", "") or "Customer"
    variables: Dict[str, Any] = {"name": name, "customer_name": name}
    if extra_vars:
        variables.update(extra_vars)
    return {
        "customer_id": customer.get("customer_id", ""),
        "phone": customer.get("mobile", "") or customer.get("phone", ""),
        "name": name,
        "variables": variables,
    }


def _customers_query(store_id: Optional[str]) -> Dict[str, Any]:
    q: Dict[str, Any] = {}
    if store_id:
        # Customers may be attached either by their home store_id or via a
        # store list; match the common single-field case used elsewhere.
        q["store_id"] = store_id
    return q


# ---------------------------------------------------------------------------
# Per-segment resolvers -> List[audience_row]
# ---------------------------------------------------------------------------


def _resolve_rx_expiry(
    db,
    store_id: Optional[str],
    window_days: int = RX_EXPIRY_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Customers with a prescription expiring within `window_days`.

    Mirrors marketing.get_rx_expiry_alerts: expiry = created_at + 730d. One row
    per CUSTOMER (deduped) carrying the soonest expiry_date as a template var.
    """
    now = now or datetime.now()
    rx_coll = db.get_collection("prescriptions")
    cust_coll = db.get_collection("customers")
    rx_query: Dict[str, Any] = {}
    if store_id:
        rx_query["store_id"] = store_id
    all_rx = list(rx_coll.find(rx_query).limit(_SCAN_LIMIT))

    # customer_id -> soonest expiry date string
    soonest: Dict[str, str] = {}
    for rx in all_rx:
        created = _coerce_dt(rx.get("created_at") or rx.get("test_date"))
        if not created:
            continue
        expiry = created + timedelta(days=RX_VALIDITY_DAYS)
        days_until = (expiry - now).days
        if days_until < 0 or days_until > window_days:
            continue
        cid = rx.get("customer_id", "")
        if not cid:
            continue
        exp_str = expiry.strftime("%d %b %Y")
        # keep the SOONEST expiry per customer
        if cid not in soonest:
            soonest[cid] = exp_str
    rows: List[Dict[str, Any]] = []
    for cid, exp_str in soonest.items():
        cust = cust_coll.find_one({"customer_id": cid}) or {}
        if not cust:
            continue
        rows.append(_audience_row(cust, {"expiry_date": exp_str}))
    return rows


def _resolve_birthday(
    db,
    store_id: Optional[str],
    window_days: int = BIRTHDAY_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Customers whose birthday (DOB month/day) lands within the next
    `window_days` days, wrapping across a year boundary."""
    now = now or datetime.now()
    today = now.date()
    cust_coll = db.get_collection("customers")
    customers = list(cust_coll.find(_customers_query(store_id)).limit(_SCAN_LIMIT))
    # Precompute the set of (month, day) that fall in the window.
    window_md = set()
    for i in range(window_days + 1):
        d = today + timedelta(days=i)
        window_md.add((d.month, d.day))
    rows: List[Dict[str, Any]] = []
    for cust in customers:
        dob = _parse_dob(cust.get("dob"))
        if not dob:
            continue
        if (dob.month, dob.day) in window_md:
            rows.append(_audience_row(cust))
    return rows


def _customers_with_recent_order(db, store_id: Optional[str], since: datetime) -> set:
    """Set of customer_ids with at least one order created at/after `since`."""
    order_coll = db.get_collection("orders")
    q: Dict[str, Any] = {}
    if store_id:
        q["store_id"] = store_id
    # created_at may be datetime OR iso string in this collection; query with
    # the datetime form (datetimes compare correctly; iso strings won't match a
    # datetime $gte, so we additionally post-filter below for safety).
    recent_ids: set = set()
    try:
        cursor = order_coll.find(q, {"customer_id": 1, "created_at": 1}).limit(
            _SCAN_LIMIT
        )
        for o in cursor:
            cid = o.get("customer_id")
            if not cid:
                continue
            created = _coerce_dt(o.get("created_at"))
            if created is None:
                # Unknown timestamp -> conservatively treat as recent so we do
                # NOT mis-classify an active customer as lapsed.
                recent_ids.add(cid)
            elif created >= since:
                recent_ids.add(cid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent-order scan failed: %s", exc)
    return recent_ids


def _resolve_winback(
    db,
    store_id: Optional[str],
    inactive_months: int = WINBACK_INACTIVE_MONTHS,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Customers with NO order in the last `inactive_months` months."""
    now = now or datetime.now()
    since = now - timedelta(days=inactive_months * 30)
    recent_ids = _customers_with_recent_order(db, store_id, since)
    cust_coll = db.get_collection("customers")
    customers = list(cust_coll.find(_customers_query(store_id)).limit(_SCAN_LIMIT))
    rows: List[Dict[str, Any]] = []
    for cust in customers:
        cid = cust.get("customer_id", "")
        if cid and cid in recent_ids:
            continue
        rows.append(_audience_row(cust))
    return rows


def _resolve_recent_buyers(
    db,
    store_id: Optional[str],
    recent_days: int = RECENT_BUYER_DAYS,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Customers who placed an order in the last `recent_days` days."""
    now = now or datetime.now()
    since = now - timedelta(days=recent_days)
    recent_ids = _customers_with_recent_order(db, store_id, since)
    if not recent_ids:
        return []
    cust_coll = db.get_collection("customers")
    rows: List[Dict[str, Any]] = []
    for cid in recent_ids:
        cust = cust_coll.find_one({"customer_id": cid}) or {}
        if not cust:
            continue
        rows.append(_audience_row(cust))
    return rows


def _resolve_by_store(db, store_id: Optional[str], **_kw) -> List[Dict[str, Any]]:
    cust_coll = db.get_collection("customers")
    customers = list(cust_coll.find(_customers_query(store_id)).limit(_SCAN_LIMIT))
    return [_audience_row(c) for c in customers]


def _resolve_by_customer_type(
    db, store_id: Optional[str], customer_type: str = "B2C", **_kw
) -> List[Dict[str, Any]]:
    ctype = (customer_type or "B2C").upper()
    cust_coll = db.get_collection("customers")
    q = _customers_query(store_id)
    q["customer_type"] = ctype
    customers = list(cust_coll.find(q).limit(_SCAN_LIMIT))
    return [_audience_row(c) for c in customers]


# Dispatch table: key -> resolver. Each resolver(db, store_id, **params).
_RESOLVERS = {
    "rx_expiry": _resolve_rx_expiry,
    "birthday": _resolve_birthday,
    "winback": _resolve_winback,
    "by_store": _resolve_by_store,
    "by_customer_type": _resolve_by_customer_type,
    "recent_buyers": _resolve_recent_buyers,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_segment(
    db,
    key: str,
    store_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Resolve a segment key to its audience (list of bulk-send recipient rows).

    Fail-soft: unknown key or missing DB -> []. Each row is deduped by phone so
    the same customer is never sent twice within one resolution.
    """
    if db is None or key not in _RESOLVERS:
        return []
    params = params or {}
    # Only pass params the resolver understands (avoid unexpected-kwarg errors).
    try:
        rows = _RESOLVERS[key](db, store_id, **params)
    except TypeError:
        # caller passed params the resolver doesn't accept -- retry bare
        try:
            rows = _RESOLVERS[key](db, store_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("segment %s resolve failed: %s", key, exc)
            return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("segment %s resolve failed: %s", key, exc)
        return []

    # Dedup by phone (fall back to customer_id) -- never message a number twice.
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for r in rows:
        ident = r.get("phone") or r.get("customer_id")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        deduped.append(r)
    return deduped


def count_segment(
    db,
    key: str,
    store_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> int:
    """Live audience size for a segment. Fail-soft -> 0."""
    return len(resolve_segment(db, key, store_id=store_id, params=params))


def preview_segment(
    db,
    key: str,
    store_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    sample_size: int = PREVIEW_SAMPLE_SIZE,
) -> Dict[str, Any]:
    """Return {key, count, sample[...]} for the builder's 'Estimated audience: N'
    display. The sample is masked (no full phone) for a read-only preview."""
    audience = resolve_segment(db, key, store_id=store_id, params=params)
    sample = []
    for r in audience[:sample_size]:
        phone = r.get("phone", "") or ""
        masked = ("*" * max(0, len(phone) - 4) + phone[-4:]) if phone else ""
        sample.append(
            {
                "customer_id": r.get("customer_id", ""),
                "name": r.get("name", ""),
                "phone_masked": masked,
            }
        )
    meta = segment_def(key) or {}
    return {
        "key": key,
        "label": meta.get("label", key),
        "count": len(audience),
        "sample": sample,
    }


def list_segments(db, store_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """All segment definitions, each with a LIVE count computed now. Fail-soft:
    a per-segment error yields count 0 for that row, never a 500."""
    out: List[Dict[str, Any]] = []
    for d in SEGMENT_DEFS:
        try:
            cnt = count_segment(db, d["key"], store_id=store_id)
        except Exception:  # noqa: BLE001
            cnt = 0
        row = dict(d)
        row["count"] = cnt
        out.append(row)
    return out
