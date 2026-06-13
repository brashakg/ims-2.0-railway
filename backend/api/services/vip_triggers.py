"""
IMS 2.0 - F43 VIP personal-triggers engine
===========================================
Centralized engine that computes WHEN a staff alert should fire for a VIP
customer's personal event (anniversary, birthday + N days, a recurring N-day
cadence, or a one-shot custom date). The whole date core is PURE and
clock-injected (``today`` is passed in) so it is fully unit-testable and
deterministic -- no hidden ``datetime.now()`` inside the math.

STAFF_ALERT slice ONLY (comms-DARK): this module + its endpoints + the
MEGAPHONE ``_scan_personal_triggers`` scan create an in-app STAFF notification
and a follow_up work-list row. The customer-MESSAGE channel for #43 stays
DEFERRED under the WhatsApp ban -- nothing leaves the building on a fresh
deploy. If a customer-facing send is ever wired, it rides notification_service
as a PENDING row gated by DISPATCH_MODE.

Trigger doc shape (``personal_triggers`` collection):
    {
      "trigger_id": "VTR-<hex>",
      "customer_id": str,
      "store_id": str | None,
      "type": one of TRIGGER_TYPES,
      "label": str,
      "base_date": "YYYY-MM-DD",     # the anchor event date
      "lead_time_days": int,          # fire this many days BEFORE the event
      "recur_every_days": int | None, # RECURRING only
      "plus_n_days": int | None,      # BIRTHDAY_PLUS_N only
      "active": bool,
      "created_by": str,
      "created_at": iso,
      "last_fired_for": str | None,   # cycle key already fired (no double-fire)
    }

No emoji (Windows cp1252). Single guarded find_one_and_update for the
no-double-fire stamp. Fail-soft reads.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Trigger taxonomy
# ---------------------------------------------------------------------------
ANNIVERSARY = "ANNIVERSARY"
BIRTHDAY_PLUS_N = "BIRTHDAY_PLUS_N"
RECURRING = "RECURRING"
CUSTOM_DATE = "CUSTOM_DATE"

TRIGGER_TYPES = (ANNIVERSARY, BIRTHDAY_PLUS_N, RECURRING, CUSTOM_DATE)

DEFAULT_LEAD_TIME_DAYS = 7
MAX_LEAD_TIME_DAYS = 365
COLLECTION = "personal_triggers"


# ---------------------------------------------------------------------------
# Pure date helpers (no clock; ``today`` is always injected)
# ---------------------------------------------------------------------------
def parse_date(value: Any) -> Optional[date]:
    """Parse a YYYY-MM-DD string (or pass a date/datetime through) -> date.

    Returns None on anything unparseable. Pure / no clock.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # Accept a leading "YYYY-MM-DD" even if a time component trails it.
    head = s[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _next_annual_occurrence(base: date, on_or_after: date) -> date:
    """The next month-day anniversary of ``base`` falling on/after ``on_or_after``.

    Feb-29 anchors fall back to Feb-28 in non-leap years.
    """
    month, day = base.month, base.day

    def _make(year: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:
            # Feb-29 in a non-leap year -> Feb-28.
            return date(year, month, 28)

    candidate = _make(on_or_after.year)
    if candidate < on_or_after:
        candidate = _make(on_or_after.year + 1)
    return candidate


def next_event_date(trigger: Dict[str, Any], today: date) -> Optional[date]:
    """The next date the underlying EVENT occurs on/after ``today`` (pure).

    This is the event itself (the anniversary/birthday/recurring tick/custom
    date) -- the staff alert fires ``lead_time_days`` BEFORE this. Returns None
    for a custom one-shot whose date has already passed, or for a bad trigger.
    """
    ttype = trigger.get("type")
    base = parse_date(trigger.get("base_date"))
    if ttype not in TRIGGER_TYPES or base is None:
        return None

    if ttype == ANNIVERSARY:
        return _next_annual_occurrence(base, today)

    if ttype == BIRTHDAY_PLUS_N:
        plus_n = _safe_int(trigger.get("plus_n_days"), 0)
        # The celebrated day is the birthday's month-day shifted by +N days.
        # Compute against the next birthday anniversary, then add N. If that
        # already passed this year, roll to next year's birthday + N.
        bday = _next_annual_occurrence(base, today)
        celebrated = bday + timedelta(days=plus_n)
        if celebrated < today:
            next_bday = _next_annual_occurrence(base, bday + timedelta(days=1))
            celebrated = next_bday + timedelta(days=plus_n)
        return celebrated

    if ttype == RECURRING:
        every = _safe_int(trigger.get("recur_every_days"), 0)
        if every <= 0:
            return None
        if base >= today:
            return base
        # First tick on/after today: base + k*every.
        delta_days = (today - base).days
        k = (delta_days + every - 1) // every  # ceil(delta/every)
        return base + timedelta(days=k * every)

    # CUSTOM_DATE: one-shot. Only fires if still in the future (or today).
    if base >= today:
        return base
    return None


def next_fire_date(trigger: Dict[str, Any], today: date) -> Optional[date]:
    """The next date the STAFF ALERT should fire (pure).

    = next_event_date - lead_time_days. For a recurring/annual trigger, if the
    fire date for the *next* event is already in the past relative to ``today``
    (i.e. we are inside the lead window already), that next event's fire date is
    still returned -- callers use ``is_due`` to decide whether to act now.
    """
    event = next_event_date(trigger, today)
    if event is None:
        return None
    lead = _safe_int(trigger.get("lead_time_days"), DEFAULT_LEAD_TIME_DAYS)
    if lead < 0:
        lead = 0
    return event - timedelta(days=lead)


def cycle_key(trigger: Dict[str, Any], today: date) -> Optional[str]:
    """A stable string identifying THIS firing cycle, used to stop a re-scan in
    the same window from double-firing. It is the ISO date of the EVENT the
    pending fire belongs to (so each anniversary/recurring tick fires once)."""
    event = next_event_date(trigger, today)
    if event is None:
        return None
    return event.isoformat()


def is_due(trigger: Dict[str, Any], today: date) -> bool:
    """True if the alert's fire window is reached for an ACTIVE trigger and this
    cycle has not already been fired (the ``last_fired_for`` guard).

    Window: today is in [fire_date, event_date]. Inactive triggers never fire.
    """
    if not trigger.get("active", True):
        return False
    event = next_event_date(trigger, today)
    if event is None:
        return False
    fire = next_fire_date(trigger, today)
    if fire is None:
        return False
    if not (fire <= today <= event):
        return False
    # No double-fire: the event-keyed cycle must not already be stamped.
    key = cycle_key(trigger, today)
    if key is not None and trigger.get("last_fired_for") == key:
        return False
    return True


# ---------------------------------------------------------------------------
# Validation (used by the router; raises ValueError on bad input)
# ---------------------------------------------------------------------------
def validate_trigger_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalize a create/update payload. Raises ValueError (the
    router maps it to HTTP 422). Returns the cleaned field subset."""
    ttype = payload.get("type")
    if ttype not in TRIGGER_TYPES:
        raise ValueError(f"type must be one of {', '.join(TRIGGER_TYPES)}")

    base = parse_date(payload.get("base_date"))
    if base is None:
        raise ValueError("base_date must be a valid YYYY-MM-DD date")

    lead = payload.get("lead_time_days", DEFAULT_LEAD_TIME_DAYS)
    lead = _safe_int(lead, DEFAULT_LEAD_TIME_DAYS)
    if lead < 0 or lead > MAX_LEAD_TIME_DAYS:
        raise ValueError(f"lead_time_days must be between 0 and {MAX_LEAD_TIME_DAYS}")

    cleaned: Dict[str, Any] = {
        "type": ttype,
        "base_date": base.isoformat(),
        "lead_time_days": lead,
        "label": str(payload.get("label") or "")[:120],
        "recur_every_days": None,
        "plus_n_days": None,
    }

    if ttype == RECURRING:
        every = _safe_int(payload.get("recur_every_days"), 0)
        if every <= 0:
            raise ValueError(
                "recur_every_days must be a positive integer for a RECURRING trigger"
            )
        cleaned["recur_every_days"] = every

    if ttype == BIRTHDAY_PLUS_N:
        plus_n = _safe_int(payload.get("plus_n_days"), -1)
        if plus_n < 0:
            raise ValueError(
                "plus_n_days must be a non-negative integer for a BIRTHDAY_PLUS_N trigger"
            )
        cleaned["plus_n_days"] = plus_n

    return cleaned


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def new_trigger_id() -> str:
    return "VTR-" + uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Data-layer CRUD (db/collection injected -> testable against a fake Mongo)
# ---------------------------------------------------------------------------
def _coll(db):
    if db is None:
        return None
    try:
        return db.get_collection(COLLECTION)
    except Exception:  # noqa: BLE001
        return None


def create_trigger(
    db,
    customer_id: str,
    payload: Dict[str, Any],
    created_by: str,
    store_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate + persist a personal trigger. Raises ValueError on bad input."""
    cleaned = validate_trigger_payload(payload)
    coll = _coll(db)
    if coll is None:
        raise RuntimeError("database unavailable")
    doc = {
        "trigger_id": new_trigger_id(),
        "customer_id": customer_id,
        "store_id": store_id,
        "active": True,
        "created_by": created_by,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": None,
        "last_fired_for": None,
        **cleaned,
    }
    coll.insert_one(doc)
    doc.pop("_id", None)
    return doc


def list_triggers(
    db,
    customer_id: Optional[str] = None,
    store_id: Optional[str] = None,
    active_only: bool = False,
) -> List[Dict[str, Any]]:
    """List triggers, fail-soft to []. Filters by customer/store when given."""
    coll = _coll(db)
    if coll is None:
        return []
    query: Dict[str, Any] = {}
    if customer_id:
        query["customer_id"] = customer_id
    if store_id:
        query["store_id"] = store_id
    if active_only:
        query["active"] = True
    try:
        rows = list(coll.find(query, {"_id": 0}))
    except Exception:  # noqa: BLE001
        return []
    return rows


def get_trigger(db, trigger_id: str) -> Optional[Dict[str, Any]]:
    coll = _coll(db)
    if coll is None:
        return None
    try:
        doc = coll.find_one({"trigger_id": trigger_id}, {"_id": 0})
    except Exception:  # noqa: BLE001
        return None
    return doc


def update_trigger(
    db, trigger_id: str, payload: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Edit/deactivate a trigger. If trigger-shape fields are present they are
    re-validated. Returns the updated doc, or None if not found. Raises
    ValueError on bad field values."""
    coll = _coll(db)
    if coll is None:
        raise RuntimeError("database unavailable")
    existing = coll.find_one({"trigger_id": trigger_id})
    if not existing:
        return None

    updates: Dict[str, Any] = {"updated_at": datetime.utcnow().isoformat()}

    # Toggle active without touching the date fields.
    if "active" in payload and payload["active"] is not None:
        updates["active"] = bool(payload["active"])

    # If any trigger-shape field is supplied, re-validate the whole shape using
    # the existing doc as the base so partial edits stay coherent.
    shape_keys = {
        "type",
        "base_date",
        "lead_time_days",
        "recur_every_days",
        "plus_n_days",
        "label",
    }
    if shape_keys & set(payload.keys()):
        merged = {k: existing.get(k) for k in shape_keys}
        for k in shape_keys:
            if k in payload and payload[k] is not None:
                merged[k] = payload[k]
        cleaned = validate_trigger_payload(merged)
        updates.update(cleaned)
        # A re-shaped trigger gets a fresh firing cycle.
        updates["last_fired_for"] = None

    coll.find_one_and_update({"trigger_id": trigger_id}, {"$set": updates})
    doc = coll.find_one({"trigger_id": trigger_id}, {"_id": 0})
    return doc


def delete_trigger(db, trigger_id: str) -> bool:
    coll = _coll(db)
    if coll is None:
        return False
    try:
        res = coll.delete_one({"trigger_id": trigger_id})
        return bool(getattr(res, "deleted_count", 0))
    except Exception:  # noqa: BLE001
        return False


def claim_fire(db, trigger_id: str, key: str) -> bool:
    """Atomically stamp ``last_fired_for=key`` IFF it is not already that key.

    A single guarded find_one_and_update so two concurrent scans (or a re-scan
    in the same cycle) can mark a trigger fired EXACTLY once -- the winner gets
    a doc back, the loser gets None. Returns True if THIS caller claimed it.
    """
    coll = _coll(db)
    if coll is None:
        return False
    try:
        won = coll.find_one_and_update(
            {"trigger_id": trigger_id, "last_fired_for": {"$ne": key}},
            {
                "$set": {
                    "last_fired_for": key,
                    "last_fired_at": datetime.utcnow().isoformat(),
                }
            },
        )
    except Exception:  # noqa: BLE001
        return False
    return won is not None


# ---------------------------------------------------------------------------
# Customer VIP profile (vip_tags / personal_notes / vip_override on the doc)
# ---------------------------------------------------------------------------
_MAX_VIP_TAGS = 20
_MAX_TAG_LEN = 50
_MAX_NOTES = 200


def _clean_vip_tag(raw: Any) -> str:
    s = str(raw or "").strip()
    return s[:_MAX_TAG_LEN]


def build_vip_update(
    existing: Dict[str, Any],
    *,
    vip_tags: Optional[List[str]] = None,
    vip_override: Optional[bool] = None,
    note_text: Optional[str] = None,
    note_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Pure: compute the $set update for a customer's VIP profile from the
    existing doc + the requested changes. Tags are de-duped + capped; a note is
    appended (never replaces). Returns the field map to persist."""
    out: Dict[str, Any] = {}

    if vip_tags is not None:
        cleaned: List[str] = []
        for raw in vip_tags:
            c = _clean_vip_tag(raw)
            if c and c not in cleaned:
                cleaned.append(c)
            if len(cleaned) >= _MAX_VIP_TAGS:
                break
        out["vip_tags"] = cleaned

    if vip_override is not None:
        out["vip_override"] = bool(vip_override)

    if note_text:
        notes = list(existing.get("personal_notes") or [])
        notes.append(
            {
                "note": str(note_text)[:1000],
                "by": note_by,
                "at": datetime.utcnow().isoformat(),
            }
        )
        out["personal_notes"] = notes[-_MAX_NOTES:]

    return out


def read_vip_profile(customer: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: extract the VIP profile slice from a customer doc (fail-soft)."""
    customer = customer or {}
    return {
        "customer_id": customer.get("customer_id"),
        "name": customer.get("name") or customer.get("full_name") or "",
        "vip_tags": list(customer.get("vip_tags") or []),
        "vip_override": bool(customer.get("vip_override") or False),
        "personal_notes": list(customer.get("personal_notes") or []),
    }


# ---------------------------------------------------------------------------
# Indexes (startup, fail-soft)
# ---------------------------------------------------------------------------
def ensure_indexes(db) -> None:
    """Create the personal_triggers indexes. Idempotent + fail-soft."""
    coll = _coll(db)
    if coll is None:
        return
    try:
        coll.create_index([("store_id", 1), ("active", 1)], name="vtr_store_active")
    except Exception:  # noqa: BLE001
        pass
    try:
        coll.create_index([("customer_id", 1)], name="vtr_customer")
    except Exception:  # noqa: BLE001
        pass
    try:
        coll.create_index([("trigger_id", 1)], name="vtr_trigger_id", unique=True)
    except Exception:  # noqa: BLE001
        pass
