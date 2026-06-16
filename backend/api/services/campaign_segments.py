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

# IST (TZ-P3): segment "today" defaults must be the IST business day -- the
# server clock is UTC, which between 00:00-05:30 IST is still on yesterday.
from api.utils.ist import now_ist_naive

logger = logging.getLogger(__name__)

# Default tuning knobs (overridable per call where it makes sense).
RX_EXPIRY_WINDOW_DAYS = 90
RX_VALIDITY_DAYS = 730  # 2-year prescription validity, matches marketing.py
BIRTHDAY_WINDOW_DAYS = 7
WINBACK_INACTIVE_MONTHS = 6
RECENT_BUYER_DAYS = 30
# E6: contact-lens reorder cadence (days since last CL purchase). Disposables
# typically need replacement every ~30 days; configurable per-call/per-rule.
CL_REORDER_CADENCE_DAYS = 30
# E6: churn-risk lapse window -- bought before but silent this long. Distinct
# from win-back (6 months); this is the earlier lapse signal.
CHURN_RISK_DAYS = 90
# Item-type tokens that count as a contact-lens line on an order.
_CL_ITEM_TYPES = {"CONTACT_LENS", "CONTACT_LENSES", "CL", "CONTACTLENS"}
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
    # --- E6 reminder-rail segments (additive) ---------------------------------
    {
        "key": "cl_reorder",
        "label": "Contact-lens reorder due",
        "description": "Customers with a contact-lens order whose last CL purchase "
        "is older than the reorder cadence (default 30 days).",
        "default_channel": "WHATSAPP",
        "default_template_id": "ANNUAL_CHECKUP_REMINDER",
        "campaign_type": "custom",
        "store_scoped": True,
    },
    {
        "key": "churn_risk",
        "label": "Churn risk (lapse)",
        "description": "Customers who bought before but have no order in the last "
        "90 days (earlier lapse signal than the 6-month win-back).",
        "default_channel": "WHATSAPP",
        "default_template_id": "WALKOUT_RECOVERY",
        "campaign_type": "winback",
        "store_scoped": True,
    },
    {
        "key": "fu_due_today",
        "label": "Follow-ups due today",
        "description": "Customers with a pending follow-up scheduled for today or "
        "earlier. CALL / IN-PERSON modes route to a staff task; WHATSAPP / SMS "
        "modes route to an outbound message.",
        "default_channel": "WHATSAPP",
        "default_template_id": "ANNUAL_CHECKUP_REMINDER",
        "campaign_type": "custom",
        "store_scoped": True,
    },
    # --- F41 lapsed-patient reactivation (#41) --------------------------------
    {
        "key": "lapsed_patient",
        "label": "Lapsed patients (reactivation)",
        "description": "Patients with NEITHER a confirmed order NOR a prescription "
        "exam in the lapse window (default 24 months). Clinically lapsed -- their "
        "Rx has likely expired and they have not returned. Surfaces an in-app "
        "reactivation work-list for staff (CALL); sends NO message (WhatsApp ban).",
        "default_channel": "CALL",
        "default_template_id": "WALKOUT_RECOVERY",
        "campaign_type": "winback",
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
        # Customers are written with home_store_id / preferred_store_id /
        # primary_store_id / store_ids -- NEVER a bare `store_id`. Filtering on
        # store_id matched zero customers, so every store-scoped segment
        # (by_store / birthday / winback / by_customer_type / lapsed_patient)
        # resolved to an empty audience. Match the canonical keys (mirrors
        # customer_repository.search_customers / customers.list_customers).
        q["$or"] = [
            {"home_store_id": store_id},
            {"preferred_store_id": store_id},
            {"primary_store_id": store_id},
            {"store_ids": store_id},
            # Legacy/edge fallback: a bare store_id. The real create-customer path
            # never sets this on a customer doc, so it matches nothing extra in
            # prod -- but keeps any legacy/imported doc that carries it in scope.
            {"store_id": store_id},
        ]
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
    now = now or now_ist_naive()
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


def _is_cl_order(order: Dict[str, Any]) -> bool:
    """True if an order contains at least one contact-lens line. Reads
    items[].item_type / category (item_type is authoritative at POS)."""
    for it in order.get("items", []) or []:
        token = str(it.get("item_type") or it.get("category") or "").strip().upper()
        if token in _CL_ITEM_TYPES:
            return True
    return False


def _resolve_cl_reorder(
    db,
    store_id: Optional[str],
    cadence_days: int = CL_REORDER_CADENCE_DAYS,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Customers who bought contact lenses and whose MOST-RECENT CL order is
    older than `cadence_days` -- i.e. they are due to reorder. One row per
    customer carrying the days-since-last-CL as a template var.
    """
    now = now or datetime.now()
    cutoff = now - timedelta(days=cadence_days)
    order_coll = db.get_collection("orders")
    cust_coll = db.get_collection("customers")
    q: Dict[str, Any] = {}
    if store_id:
        q["store_id"] = store_id
    # customer_id -> latest CL order datetime
    latest_cl: Dict[str, datetime] = {}
    try:
        for o in order_coll.find(q).limit(_SCAN_LIMIT):
            if not _is_cl_order(o):
                continue
            cid = o.get("customer_id")
            if not cid:
                continue
            created = _coerce_dt(o.get("created_at"))
            if created is None:
                continue
            if cid not in latest_cl or created > latest_cl[cid]:
                latest_cl[cid] = created
    except Exception as exc:  # noqa: BLE001
        logger.warning("cl_reorder scan failed: %s", exc)
        return []
    rows: List[Dict[str, Any]] = []
    for cid, last_dt in latest_cl.items():
        if last_dt > cutoff:
            continue  # bought CL recently -> not due yet
        cust = cust_coll.find_one({"customer_id": cid}) or {}
        if not cust:
            continue
        days_since = (now - last_dt).days
        rows.append(_audience_row(cust, {"days_since_cl": days_since}))
    return rows


# F41: order statuses that count as a REAL confirmed sale -- a DRAFT/CANCELLED
# order does NOT keep a patient "active". Mirrors crm._SOLD_STATUSES intent but
# expressed as the exclusion the resolver needs (anything not draft/cancelled is
# a real touch). Lapse threshold default (24 months) lives in E2 policy.
LAPSED_THRESHOLD_MONTHS = 24
_NON_SALE_STATUSES = {"DRAFT", "draft", "Draft", "CANCELLED", "cancelled", "Cancelled"}


def _customers_with_recent_exam(db, store_id: Optional[str], since: datetime) -> set:
    """Set of customer_ids with at least one prescription EXAM at/after `since`.

    A prescription exam (clinical visit) keeps a patient "active" for
    reactivation purposes even with no purchase. Reads prescriptions.created_at /
    prescription_date / test_date (the codebase writes any of these). An unknown
    timestamp is treated as recent (conservative -- never mis-flag a recently
    examined patient as lapsed). Fail-soft -> empty set."""
    recent_ids: set = set()
    try:
        rx_coll = db.get_collection("prescriptions")
    except Exception:  # noqa: BLE001
        return recent_ids
    q: Dict[str, Any] = {}
    if store_id:
        q["store_id"] = store_id
    try:
        for rx in rx_coll.find(
            q, {"customer_id": 1, "created_at": 1, "prescription_date": 1, "test_date": 1}
        ).limit(_SCAN_LIMIT):
            cid = rx.get("customer_id")
            if not cid:
                continue
            created = _coerce_dt(
                rx.get("prescription_date") or rx.get("created_at") or rx.get("test_date")
            )
            if created is None or created >= since:
                recent_ids.add(cid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent-exam scan failed: %s", exc)
    return recent_ids


def _customers_with_recent_sale(db, store_id: Optional[str], since: datetime) -> set:
    """Set of customer_ids with at least one CONFIRMED order (not DRAFT/CANCELLED)
    at/after `since`. Distinct from `_customers_with_recent_order` which counts
    every order regardless of status -- a lapsed-patient gate must ignore an
    abandoned draft. Unknown timestamp -> treated as recent (conservative)."""
    recent_ids: set = set()
    try:
        order_coll = db.get_collection("orders")
    except Exception:  # noqa: BLE001
        return recent_ids
    q: Dict[str, Any] = {}
    if store_id:
        q["store_id"] = store_id
    try:
        for o in order_coll.find(
            q, {"customer_id": 1, "created_at": 1, "status": 1}
        ).limit(_SCAN_LIMIT):
            cid = o.get("customer_id")
            if not cid:
                continue
            if str(o.get("status") or "") in _NON_SALE_STATUSES:
                continue  # a draft/cancelled order is not a real touch
            created = _coerce_dt(o.get("created_at"))
            if created is None or created >= since:
                recent_ids.add(cid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent-sale scan failed: %s", exc)
    return recent_ids


def _resolve_lapsed_patient(
    db,
    store_id: Optional[str],
    lapse_threshold_months: int = LAPSED_THRESHOLD_MONTHS,
    now: Optional[datetime] = None,
    **_kw,
) -> List[Dict[str, Any]]:
    """F41 -- patients with NEITHER a confirmed order NOR a prescription exam in
    the last `lapse_threshold_months` months. These are CLINICALLY lapsed: their
    Rx has likely expired and they have not returned.

    Dual-gap, ANDed: a patient is lapsed only when BOTH the no-recent-sale gate
    AND the no-recent-exam gate hold. A patient who had an exam but never bought
    is NOT lapsed if the exam is recent (they are clinically engaged); a patient
    who bought recently is NOT lapsed even if their last exam is old. Absence of
    any record at all = infinitely lapsed (qualifies).

    Each audience row carries `months_lapsed` (whole months since the patient's
    most recent touch of EITHER kind) so the work-list can rank most-lapsed first.

    REUSES the merged scan helpers -- no new signal logic, no fork. marketing_consent
    is NOT filtered here (campaign_segments contract: consent is a SEND-time gate;
    F41 is dark / in-app, but the contract is preserved for parity)."""
    now = now or datetime.now()
    cutoff = now - timedelta(days=lapse_threshold_months * 30)
    recent_sale = _customers_with_recent_sale(db, store_id, cutoff)
    recent_exam = _customers_with_recent_exam(db, store_id, cutoff)
    active = recent_sale | recent_exam  # active if EITHER touch is recent

    # Build a per-customer "most recent touch" map so we can report months_lapsed
    # for the lapsed ones (only needed for the lapsed set, but computed in one
    # pass over the same capped scans).
    last_touch: Dict[str, datetime] = {}

    def _note(cid: str, dt: Optional[datetime]):
        if not cid or dt is None:
            return
        if cid not in last_touch or dt > last_touch[cid]:
            last_touch[cid] = dt

    try:
        order_coll = db.get_collection("orders")
        oq: Dict[str, Any] = {}
        if store_id:
            oq["store_id"] = store_id
        for o in order_coll.find(
            oq, {"customer_id": 1, "created_at": 1, "status": 1}
        ).limit(_SCAN_LIMIT):
            if str(o.get("status") or "") in _NON_SALE_STATUSES:
                continue
            _note(o.get("customer_id"), _coerce_dt(o.get("created_at")))
        rx_coll = db.get_collection("prescriptions")
        rq: Dict[str, Any] = {}
        if store_id:
            rq["store_id"] = store_id
        for rx in rx_coll.find(
            rq, {"customer_id": 1, "created_at": 1, "prescription_date": 1, "test_date": 1}
        ).limit(_SCAN_LIMIT):
            _note(
                rx.get("customer_id"),
                _coerce_dt(rx.get("prescription_date") or rx.get("created_at") or rx.get("test_date")),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("lapsed_patient touch scan failed: %s", exc)

    cust_coll = db.get_collection("customers")
    customers = list(cust_coll.find(_customers_query(store_id)).limit(_SCAN_LIMIT))
    rows: List[Dict[str, Any]] = []
    for cust in customers:
        cid = cust.get("customer_id", "")
        if not cid or cid in active:
            continue  # active (recent sale OR recent exam) -> not lapsed
        touch = last_touch.get(cid)
        if touch is not None:
            months = max(lapse_threshold_months, int((now - touch).days // 30))
        else:
            months = None  # no record at all -> infinitely lapsed
        rows.append(
            _audience_row(
                cust,
                {
                    "months_lapsed": months,
                    "last_touch_date": touch.date().isoformat() if touch else None,
                },
            )
        )
    return rows


def _resolve_churn_risk(
    db,
    store_id: Optional[str],
    inactive_days: int = CHURN_RISK_DAYS,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Customers who HAVE ordered before but have NO order in the last
    `inactive_days` (default 90). Distinct from `winback` (6 months) -- this is
    the earlier lapse signal. A customer with zero orders ever is NOT churn risk
    (they were never engaged); they belong to other acquisition segments.
    """
    now = now or datetime.now()
    since = now - timedelta(days=inactive_days)
    order_coll = db.get_collection("orders")
    cust_coll = db.get_collection("customers")
    q: Dict[str, Any] = {}
    if store_id:
        q["store_id"] = store_id
    # customer_id -> (ever_ordered, ordered_recently)
    ever: set = set()
    recent: set = set()
    try:
        for o in order_coll.find(
            q, {"customer_id": 1, "created_at": 1}
        ).limit(_SCAN_LIMIT):
            cid = o.get("customer_id")
            if not cid:
                continue
            ever.add(cid)
            created = _coerce_dt(o.get("created_at"))
            if created is None or created >= since:
                # Unknown timestamp -> conservatively treat as recent (do NOT
                # mis-flag an active customer as churning).
                recent.add(cid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("churn_risk scan failed: %s", exc)
        return []
    lapsed = ever - recent
    rows: List[Dict[str, Any]] = []
    for cid in lapsed:
        cust = cust_coll.find_one({"customer_id": cid}) or {}
        if not cust:
            continue
        rows.append(_audience_row(cust))
    return rows


# Map a follow_ups.type (a PURPOSE enum, NOT a channel -- CORRECTIONS P1) to a
# delivery mode. The pre-existing follow_ups collection has no `mode` field; we
# honour an explicit `mode`/`channel` when present, else map the purpose to a
# sensible default. order_delivery is a service ping (WHATSAPP); the rest default
# to a CALL the staff make (safe: a task, never an un-consented auto-message).
_FU_TYPE_TO_MODE = {
    "order_delivery": "WHATSAPP",
    "prescription_expiry": "WHATSAPP",
    "eye_test_reminder": "CALL",
    "frame_replacement": "CALL",
    "general": "CALL",
}


def _resolve_walkout_fu_due_today(
    db,
    store_id: Optional[str],
    today: str,
    cust_coll,
) -> List[Dict[str, Any]]:
    """F45 D4 -- pending WALKOUT follow-up sub-docs due today or earlier.

    The walkout CRM (F45) stores its 2/3-round follow-ups as embedded
    `walkouts.followups[]` sub-docs whose `mode` IS the delivery channel
    (CALL / WHATSAPP / SMS / EMAIL / IN-PERSON) -- distinct from the generic
    `follow_ups` collection (whose `type` is a PURPOSE enum, per CORRECTIONS
    P1). This resolver reads those walkout sub-docs so the reminder rail's
    fu_due_today segment covers BOTH sources. CALL / IN-PERSON modes route to
    a staff task in evaluate_rule; WHATSAPP / SMS / EMAIL ride the dark send.
    """
    rows: List[Dict[str, Any]] = []
    try:
        wo_coll = db.get_collection("walkouts")
    except Exception:  # noqa: BLE001
        return rows
    wq: Dict[str, Any] = {"deleted_at": None}
    if store_id:
        wq["store_id"] = store_id
    try:
        walkouts = list(wo_coll.find(wq).limit(_SCAN_LIMIT))
    except Exception as exc:  # noqa: BLE001
        logger.warning("walkout fu_due_today scan failed: %s", exc)
        return rows
    for w in walkouts:
        cid = w.get("customer_id") or ""
        name = w.get("customer_name") or "Customer"
        phone = w.get("mobile") or ""
        if (not phone) and cid:
            cust = cust_coll.find_one({"customer_id": cid}) or {}
            phone = cust.get("mobile", "") or cust.get("phone", "")
        for fu in w.get("followups") or []:
            if fu.get("status") != "PENDING":
                continue
            scheduled = fu.get("scheduled_date")
            if isinstance(scheduled, datetime):
                sched_str = scheduled.date().isoformat()
            else:
                sched_str = str(scheduled)[:10] if scheduled else ""
            if not sched_str or sched_str > today:
                continue
            # The walkout FU `mode` IS the channel (no purpose-enum mapping).
            mode = str(fu.get("mode") or "CALL").strip().upper()
            rows.append(
                {
                    "customer_id": cid,
                    "phone": phone,
                    "name": name,
                    "variables": {
                        "name": name,
                        "customer_name": name,
                        "mode": mode,
                        "walkout_id": w.get("walkout_id", ""),
                        "follow_up_round": fu.get("round"),
                        "source": "walkout",
                    },
                }
            )
    return rows


def _resolve_fu_due_today(
    db,
    store_id: Optional[str],
    now: Optional[datetime] = None,
    **_kw,
) -> List[Dict[str, Any]]:
    """Customers with a PENDING follow-up scheduled for today or earlier.

    Reads BOTH follow-up sources and merges them (CORRECTIONS P1 / F45 D4):
      - the generic `follow_ups` collection (follow_ups.py GET /due-today):
        same `status=pending` + `scheduled_date <= today` filter; the FU
        `type` is a PURPOSE enum mapped to a delivery mode.
      - the F45 walkout `walkouts.followups[]` sub-docs, whose `mode` IS the
        delivery channel.
    Each audience row carries a `mode` in variables so the rule evaluator
    routes CALL / IN-PERSON to a staff task and WHATSAPP / SMS / EMAIL to
    send_notification (the dark, DISPATCH_MODE-gated path).
    """
    now = now or now_ist_naive()
    today = now.date().isoformat()
    fu_coll = db.get_collection("follow_ups")
    cust_coll = db.get_collection("customers")
    q: Dict[str, Any] = {"status": "pending", "scheduled_date": {"$lte": today}}
    if store_id:
        q["store_id"] = store_id
    rows: List[Dict[str, Any]] = []
    try:
        follow_ups = list(fu_coll.find(q).limit(_SCAN_LIMIT))
    except Exception as exc:  # noqa: BLE001
        logger.warning("fu_due_today scan failed: %s", exc)
        follow_ups = []
    for fu in follow_ups:
        cid = fu.get("customer_id", "")
        # Explicit mode/channel wins; else map the purpose enum to a mode.
        mode = (fu.get("mode") or fu.get("channel") or "").strip().upper()
        if not mode:
            mode = _FU_TYPE_TO_MODE.get(fu.get("type", "general"), "CALL")
        # Prefer the follow-up's own contact fields; fall back to the customer.
        name = fu.get("customer_name") or ""
        phone = fu.get("customer_phone") or ""
        if (not name or not phone) and cid:
            cust = cust_coll.find_one({"customer_id": cid}) or {}
            name = name or cust.get("name", "Customer")
            phone = phone or cust.get("mobile", "") or cust.get("phone", "")
        row = {
            "customer_id": cid,
            "phone": phone,
            "name": name or "Customer",
            "variables": {
                "name": name or "Customer",
                "customer_name": name or "Customer",
                "mode": mode,
                "follow_up_id": fu.get("follow_up_id", ""),
                "follow_up_type": fu.get("type", ""),
            },
        }
        rows.append(row)
    # Merge in the F45 walkout follow-up sub-docs.
    rows.extend(_resolve_walkout_fu_due_today(db, store_id, today, cust_coll))
    return rows


# Dispatch table: key -> resolver. Each resolver(db, store_id, **params).
_RESOLVERS = {
    "rx_expiry": _resolve_rx_expiry,
    "birthday": _resolve_birthday,
    "winback": _resolve_winback,
    "by_store": _resolve_by_store,
    "by_customer_type": _resolve_by_customer_type,
    "recent_buyers": _resolve_recent_buyers,
    "cl_reorder": _resolve_cl_reorder,
    "churn_risk": _resolve_churn_risk,
    "fu_due_today": _resolve_fu_due_today,
    "lapsed_patient": _resolve_lapsed_patient,
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
