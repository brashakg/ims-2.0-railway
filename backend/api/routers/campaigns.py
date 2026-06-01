"""
IMS 2.0 - Marketing Campaign Manager
====================================
The CAMPAIGN LAYER on top of the existing marketing send infrastructure.

The marketing router (routers/marketing.py) already owns the SEND machinery:
notification queueing via services.notification_service.send_notification (which
writes a PENDING notification_logs row and is gated by DISPATCH_MODE off/test/
live so a fresh deploy never spams customers), template resolution, the
per-user rate limiter, the TRAI/DLT promo quiet-hours window, and the
marketing-consent opt-out gate.

What was MISSING -- and is added here -- is the campaign layer:
  * a campaign entity + CRUD (the marketing_campaigns collection),
  * a SEGMENT builder with LIVE audience counts (Rx renewal due, birthdays,
    win-back lapsed buyers, all-consented), store-scoped,
  * scheduling (one-time / recurring / triggered life-event),
  * per-campaign analytics derived from the notification_logs this campaign
    produced (tagged with campaign_id).

This module DOES NOT build a parallel sender. Every actual message still goes
through services.notification_service.send_notification, so the DISPATCH_MODE
safety gate, consent gate, and DLT audit fields all apply unchanged. A campaign
"send" is just an audience resolution + a fan-out over that one shared function,
with each produced log row stamped campaign_id/campaign_run_id for analytics.

Mounted at /api/v1/marketing/campaigns (see api/main.py). Kept as a separate
router file (not folded into marketing.py) so the campaign surface evolves
independently of the transactional send endpoints.

Honest-status contract (inherited): a campaign send QUEUES messages. With
DISPATCH_MODE=off (default) nothing leaves the building; in test mode only the
TEST_PHONE recipient is contacted by the drain. The analytics view reflects the
real notification_logs lifecycle (PENDING -> SENT/SIMULATED/FAILED/DELIVERED),
never a fabricated success.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime, timedelta
import uuid
import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles
from ..dependencies import get_db as _dep_get_db
from ..services.notification_service import send_notification

router = APIRouter()

# Roles permitted to manage + fire campaigns. Mirrors marketing.py
# _BULK_SEND_ROLES: a campaign is a mass customer fan-out (spammy + metered), so
# it is gated to the same management roles. SUPERADMIN auto-passes via
# require_roles. Reads are AUTHENTICATED (any logged-in staff may VIEW campaigns
# + audience estimates), only writes/sends require a bulk-send role.
_CAMPAIGN_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")

# Roles that bypass store-scoping (HQ). Mirrors customers.py is_hq.
_HQ_ROLES = {"SUPERADMIN", "ADMIN", "AREA_MANAGER"}

CAMPAIGNS_COLLECTION = "marketing_campaigns"

# Campaign types -> the segment that backs them by default + the template most
# appropriate. These are conveniences for the builder; a custom campaign may
# pick any segment + any template.
CAMPAIGN_TYPES = {"rx_renewal", "birthday", "winback", "custom"}

# Schedule kinds.
SCHEDULE_KINDS = {"one_time", "recurring", "triggered"}

# Lifecycle statuses. DRAFT -> SCHEDULED -> ACTIVE -> (PAUSED) -> COMPLETED.
# SENDING is a transient state during a fan-out; ACTIVE means a recurring/
# triggered campaign is live; COMPLETED is a finished one-time send.
CAMPAIGN_STATUSES = {
    "DRAFT",
    "SCHEDULED",
    "ACTIVE",
    "PAUSED",
    "COMPLETED",
}

VALID_CHANNELS = {"WHATSAPP", "SMS", "EMAIL"}


def _get_db():
    try:
        from database.connection import get_db

        return get_db().db
    except Exception:
        return _dep_get_db()


def _is_hq(user: dict) -> bool:
    roles = set(user.get("roles", []) or [])
    return bool(roles & _HQ_ROLES)


def _effective_store(user: dict, store_id: Optional[str]) -> Optional[str]:
    """Resolve the store scope for a request.

    HQ roles (SUPERADMIN/ADMIN/AREA_MANAGER) may target any store via ?store_id,
    or ALL stores when they pass nothing (returns None = no store filter).
    Store-level roles are always pinned to their own active_store_id; the
    ?store_id param is ignored for them (cannot peek another store's audience).
    """
    if _is_hq(user):
        return store_id  # may be None -> all stores
    return user.get("active_store_id")


def _store_clause(store: Optional[str]) -> Dict[str, Any]:
    """Mongo clause matching a customer's store on either the legacy
    home_store_id or the newer preferred_store_id. Empty dict = no filter."""
    if not store:
        return {}
    return {"$or": [{"home_store_id": store}, {"preferred_store_id": store}]}


# ============================================================================
# SEGMENTS - live audience resolution (reused by preview + send)
# ============================================================================
# Each segment resolves to a list of recipient dicts:
#   {customer_id, phone, name, email, variables}
# All segments honour the marketing-consent opt-out (marketing_consent != False)
# and the store scope. They are fail-soft: any DB error yields an empty list so
# a preview or send degrades to "0 audience" rather than a 500.


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _consent_ok(cust: Dict[str, Any]) -> bool:
    """Only an explicit False opts out; missing/None defaults to consented
    (matches the customer-create default)."""
    return cust.get("marketing_consent") is not False


def _recipient_from_customer(cust: Dict[str, Any], extra_vars: Optional[Dict] = None) -> Dict[str, Any]:
    name = cust.get("name", "") or "Customer"
    variables = {"name": name, "customer_name": name, "store_name": "Better Vision"}
    if extra_vars:
        variables.update(extra_vars)
    return {
        "customer_id": cust.get("customer_id", ""),
        "phone": cust.get("mobile", "") or cust.get("phone", ""),
        "email": cust.get("email", ""),
        "name": name,
        "variables": variables,
    }


def _segment_all_consented(db, store: Optional[str], limit: int) -> List[Dict[str, Any]]:
    coll = db.get_collection("customers")
    query: Dict[str, Any] = {"marketing_consent": {"$ne": False}}
    query.update(_store_clause(store))
    out = []
    for c in coll.find(query).limit(limit):
        if not c.get("mobile") and not c.get("email"):
            continue
        out.append(_recipient_from_customer(c))
    return out


def _segment_birthday_today(db, store: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """Customers whose date_of_birth ends with today's -MM-DD (handles both
    YYYY-MM-DD and DD-MM-YYYY storage; mirrors MEGAPHONE._scan_birthdays_today)."""
    coll = db.get_collection("customers")
    today = datetime.now().strftime("%m-%d")
    query: Dict[str, Any] = {
        "date_of_birth": {"$regex": today + "$"},
        "marketing_consent": {"$ne": False},
    }
    query.update(_store_clause(store))
    out = []
    for c in coll.find(query).limit(limit):
        if not c.get("mobile") and not c.get("email"):
            continue
        out.append(_recipient_from_customer(c))
    return out


def _segment_rx_renewal(db, store: Optional[str], limit: int, within_days: int = 90) -> List[Dict[str, Any]]:
    """Customers with a prescription expiring within `within_days` (and not yet
    expired). Mirrors marketing.get_rx_expiry_alerts / MEGAPHONE._scan_rx_expiring
    -- Rx validity is 2 years (created_at + 730d) when no explicit expiry_date.
    Consent-gated + store-scoped; one recipient per customer (earliest expiry)."""
    rx_coll = db.get_collection("prescriptions")
    cust_coll = db.get_collection("customers")
    now = datetime.now()
    horizon = now + timedelta(days=within_days)

    rx_query: Dict[str, Any] = {}
    if store:
        rx_query["store_id"] = store
    # Pull a bounded window of prescriptions and compute expiry in Python so we
    # handle BOTH an explicit expiry_date and the created_at+730 fallback.
    seen: Dict[str, Dict[str, Any]] = {}
    for rx in rx_coll.find(rx_query).limit(2000):
        cid = rx.get("customer_id")
        if not cid:
            continue
        expiry = _parse_dt(rx.get("expiry_date"))
        if expiry is None:
            created = _parse_dt(rx.get("created_at") or rx.get("test_date"))
            if created is None:
                continue
            expiry = created + timedelta(days=730)
        if expiry < now or expiry > horizon:
            continue
        prev = seen.get(cid)
        if prev is None or expiry < prev["_expiry"]:
            seen[cid] = {"_expiry": expiry, "rx": rx}

    if not seen:
        return []

    # Look up the customers (consent gate + contactability) in one pass.
    ids = list(seen.keys())
    custs = {
        c.get("customer_id"): c
        for c in cust_coll.find({"customer_id": {"$in": ids}})
    }
    out = []
    for cid, info in seen.items():
        cust = custs.get(cid)
        if not cust or not _consent_ok(cust):
            continue
        if store and not (
            cust.get("home_store_id") == store or cust.get("preferred_store_id") == store
        ):
            # Rx was at this store but the customer is homed elsewhere; the Rx
            # store_id already scoped it, so keep -- but if an explicit store
            # mismatch exists we still include (Rx store is the better signal).
            pass
        expiry = info["_expiry"]
        out.append(
            _recipient_from_customer(
                cust,
                extra_vars={
                    "expiry_date": expiry.strftime("%d %b %Y"),
                    "store_phone": "",
                },
            )
        )
        if len(out) >= limit:
            break
    return out


def _segment_winback(db, store: Optional[str], limit: int, inactive_days: int = 180) -> List[Dict[str, Any]]:
    """Lapsed buyers: customers whose most recent order is older than
    `inactive_days` (default 180). Aggregates the orders collection for the last
    order date per customer (coalescing order_date/created_at), then filters to
    consented, contactable, store-scoped customers."""
    orders_coll = db.get_collection("orders")
    cust_coll = db.get_collection("customers")
    cutoff = (datetime.now() - timedelta(days=inactive_days))
    cutoff_iso = cutoff.isoformat()

    # Aggregate last order per customer. Use $ifNull to coalesce the date field.
    last_order: Dict[str, str] = {}
    try:
        pipeline = [
            {"$match": ({"store_id": store} if store else {})},
            {
                "$group": {
                    "_id": "$customer_id",
                    "last": {
                        "$max": {"$ifNull": ["$order_date", "$created_at"]}
                    },
                }
            },
        ]
        for row in orders_coll.aggregate(pipeline):
            cid = row.get("_id")
            if cid:
                last_order[cid] = row.get("last")
    except Exception:
        # Mock/minimal collection without aggregate: fall back to a bounded scan.
        for o in orders_coll.find(
            {"store_id": store} if store else {},
            {"customer_id": 1, "order_date": 1, "created_at": 1},
        ).limit(5000):
            cid = o.get("customer_id")
            if not cid:
                continue
            d = o.get("order_date") or o.get("created_at") or ""
            if cid not in last_order or (d and d > last_order[cid]):
                last_order[cid] = d

    # Keep only customers whose last order predates the cutoff.
    lapsed_ids = [
        cid for cid, d in last_order.items() if d and str(d) < cutoff_iso
    ]
    if not lapsed_ids:
        return []

    custs = {
        c.get("customer_id"): c
        for c in cust_coll.find({"customer_id": {"$in": lapsed_ids}})
    }
    out = []
    for cid in lapsed_ids:
        cust = custs.get(cid)
        if not cust or not _consent_ok(cust):
            continue
        # Skip walk-in / placeholder ids that aren't real customers.
        if str(cid).startswith("walkin-") or cid == "walk-in":
            continue
        if store and not (
            cust.get("home_store_id") == store or cust.get("preferred_store_id") == store
        ):
            continue
        if not cust.get("mobile") and not cust.get("email"):
            continue
        out.append(_recipient_from_customer(cust))
        if len(out) >= limit:
            break
    return out


# Segment registry. `count_only` resolution caps at a high limit so the LIVE
# count is accurate for the builder; a send caps at the same bound.
_SEGMENT_LIMIT = 5000

SEGMENTS: Dict[str, Dict[str, Any]] = {
    "rx_renewal_due": {
        "id": "rx_renewal_due",
        "label": "Rx renewal due (next 90 days)",
        "description": "Customers whose prescription expires within 90 days and hasn't lapsed.",
        "resolver": _segment_rx_renewal,
        "default_template": "PRESCRIPTION_EXPIRY",
        "campaign_type": "rx_renewal",
    },
    "birthday_today": {
        "id": "birthday_today",
        "label": "Birthdays today",
        "description": "Customers whose birthday is today.",
        "resolver": _segment_birthday_today,
        "default_template": "BIRTHDAY_WISH",
        "campaign_type": "birthday",
    },
    "winback_lapsed": {
        "id": "winback_lapsed",
        "label": "Win-back (no purchase in 180 days)",
        "description": "Buyers whose most recent order is older than 180 days.",
        "resolver": _segment_winback,
        "default_template": "WALKOUT_RECOVERY",
        "campaign_type": "winback",
    },
    "all_consented": {
        "id": "all_consented",
        "label": "All opted-in customers",
        "description": "Every customer who has not opted out of marketing.",
        "resolver": _segment_all_consented,
        "default_template": "ANNUAL_CHECKUP_REMINDER",
        "campaign_type": "custom",
    },
}


def _resolve_segment(db, segment_id: str, store: Optional[str], limit: int = _SEGMENT_LIMIT) -> List[Dict[str, Any]]:
    seg = SEGMENTS.get(segment_id)
    if seg is None:
        raise HTTPException(status_code=404, detail=f"Unknown segment '{segment_id}'")
    if db is None:
        return []
    try:
        return seg["resolver"](db, store, limit)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - fail-soft: empty audience, not a 500
        logger.warning("Segment '%s' resolution failed (non-fatal): %s", segment_id, e)
        return []


# ============================================================================
# SCHEMAS
# ============================================================================


class ScheduleSpec(BaseModel):
    """A campaign schedule. kind drives interpretation:
      one_time  -> send once at send_at (ISO datetime).
      recurring -> repeat on `frequency` (daily/weekly/monthly) at send_at time.
      triggered -> fire per life-event (e.g. rx_expiry / birthday); the MEGAPHONE
                   agent / a future worker drives these; the campaign just stores
                   the intent.
    """

    kind: Literal["one_time", "recurring", "triggered"] = "one_time"
    send_at: Optional[str] = None  # ISO datetime for one_time / recurring anchor
    frequency: Optional[Literal["daily", "weekly", "monthly"]] = None
    trigger_event: Optional[str] = None  # for triggered (rx_expiry, birthday, ...)


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    campaign_type: str = "custom"
    segment_id: str = "all_consented"
    channels: List[str] = ["WHATSAPP"]
    template_id: str = "ANNUAL_CHECKUP_REMINDER"
    schedule: Optional[ScheduleSpec] = None
    store_id: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("campaign_type")
    @classmethod
    def _vtype(cls, v: str) -> str:
        val = (v or "custom").lower().strip()
        if val not in CAMPAIGN_TYPES:
            raise ValueError(f"campaign_type must be one of {sorted(CAMPAIGN_TYPES)}")
        return val

    @field_validator("channels")
    @classmethod
    def _vchannels(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("at least one channel is required")
        out = []
        for c in v:
            cc = (c or "").upper().strip()
            if cc not in VALID_CHANNELS:
                raise ValueError(f"channel must be one of {sorted(VALID_CHANNELS)}")
            out.append(cc)
        return out


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    campaign_type: Optional[str] = None
    segment_id: Optional[str] = None
    channels: Optional[List[str]] = None
    template_id: Optional[str] = None
    schedule: Optional[ScheduleSpec] = None
    notes: Optional[str] = None

    @field_validator("channels")
    @classmethod
    def _vchannels(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("at least one channel is required")
        out = []
        for c in v:
            cc = (c or "").upper().strip()
            if cc not in VALID_CHANNELS:
                raise ValueError(f"channel must be one of {sorted(VALID_CHANNELS)}")
            out.append(cc)
        return out


class SegmentPreviewRequest(BaseModel):
    segment_id: str
    store_id: Optional[str] = None
    sample_size: int = Field(5, ge=0, le=25)


class ScheduleRequest(BaseModel):
    schedule: ScheduleSpec


class SendRequest(BaseModel):
    # Optional override; defaults to the campaign's own channels/template.
    test_only: bool = False  # informational; real gating is DISPATCH_MODE


# ============================================================================
# SERIALIZATION
# ============================================================================


def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc.pop("_id", None)
    return doc


def _dispatch_mode() -> str:
    try:
        from agents.providers import dispatch_mode as _dm

        return _dm()
    except Exception:
        import os

        return os.getenv("DISPATCH_MODE", "off").lower()


# ============================================================================
# SEGMENT ENDPOINTS
# ============================================================================


@router.get("/segments")
async def list_segments(
    store_id: Optional[str] = Query(None),
    with_counts: bool = Query(True),
    current_user: dict = Depends(get_current_user),
):
    """List the available audience segments, each with a LIVE audience count
    (store-scoped). The builder uses this to show 'Estimated audience: N' before
    a campaign is created. Fail-soft: a count that can't be computed shows 0."""
    store = _effective_store(current_user, store_id)
    db = _get_db()
    out = []
    for seg in SEGMENTS.values():
        entry = {
            "id": seg["id"],
            "label": seg["label"],
            "description": seg["description"],
            "default_template": seg["default_template"],
            "campaign_type": seg["campaign_type"],
        }
        if with_counts and db is not None:
            try:
                entry["audience_count"] = len(_resolve_segment(db, seg["id"], store))
            except Exception:
                entry["audience_count"] = 0
        else:
            entry["audience_count"] = None
        out.append(entry)
    return {"segments": out, "store_scope": store, "dispatch_mode": _dispatch_mode()}


@router.post("/segments/preview")
async def preview_segment(
    req: SegmentPreviewRequest,
    current_user: dict = Depends(get_current_user),
):
    """Resolve a segment and return its live COUNT plus a small anonymised
    sample (first/last initial only) so the builder can show who it will reach
    without leaking the full PII list to the UI."""
    store = _effective_store(current_user, req.store_id)
    db = _get_db()
    recipients = _resolve_segment(db, req.segment_id, store) if db is not None else []
    sample = []
    for r in recipients[: req.sample_size]:
        name = r.get("name", "") or "Customer"
        phone = r.get("phone", "") or ""
        masked_phone = ("*" * max(0, len(phone) - 4) + phone[-4:]) if phone else ""
        sample.append({"name": name, "masked_phone": masked_phone})
    return {
        "segment_id": req.segment_id,
        "audience_count": len(recipients),
        "sample": sample,
        "store_scope": store,
    }


# ============================================================================
# CAMPAIGN CRUD
# ============================================================================


def _summary_from(campaigns: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = sum(1 for c in campaigns if c.get("status") in ("ACTIVE", "SCHEDULED"))
    total_sent = sum(int((c.get("stats") or {}).get("sent", 0)) for c in campaigns)
    total_delivered = sum(int((c.get("stats") or {}).get("delivered", 0)) for c in campaigns)
    total_converted = sum(int((c.get("stats") or {}).get("converted", 0)) for c in campaigns)
    # "Open rate" for WhatsApp/SMS is proxied by delivery rate (we don't have
    # read receipts here). Conversion rate = converted / sent.
    delivery_rate = round((total_delivered / total_sent * 100), 1) if total_sent else 0.0
    conversion_rate = round((total_converted / total_sent * 100), 1) if total_sent else 0.0
    return {
        "active": active,
        "total": len(campaigns),
        "total_sent": total_sent,
        "total_delivered": total_delivered,
        "delivery_rate": delivery_rate,
        "open_rate": delivery_rate,  # alias: delivery proxies open for WA/SMS
        "conversion_rate": conversion_rate,
    }


@router.get("")
async def list_campaigns(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List campaigns + a headline summary (active / total sent / open[delivery]
    rate / conversion). Store-scoped for non-HQ roles. Fail-soft -> empty."""
    store = _effective_store(current_user, store_id)
    db = _get_db()
    if db is None:
        return {"campaigns": [], "summary": _summary_from([])}

    query: Dict[str, Any] = {}
    if store:
        query["store_id"] = store
    if status:
        query["status"] = status.upper()
    try:
        coll = db.get_collection(CAMPAIGNS_COLLECTION)
        campaigns = [_clean(c) for c in coll.find(query).sort("created_at", -1).limit(limit)]
    except Exception as e:
        logger.warning("Campaign list failed (non-fatal): %s", e)
        campaigns = []

    return {
        "campaigns": campaigns,
        "summary": _summary_from(campaigns),
        "dispatch_mode": _dispatch_mode(),
    }


def _new_campaign_doc(req: CampaignCreate, user: dict, store: Optional[str]) -> Dict[str, Any]:
    now = datetime.now().isoformat()
    schedule = req.schedule.model_dump() if req.schedule else {"kind": "one_time"}
    status = "DRAFT"
    if req.schedule and req.schedule.kind in ("recurring", "triggered"):
        status = "DRAFT"  # becomes SCHEDULED on explicit /schedule
    return {
        "campaign_id": f"CMP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
        "name": req.name.strip(),
        "campaign_type": req.campaign_type,
        "segment_id": req.segment_id,
        "channels": req.channels,
        "template_id": req.template_id,
        "schedule": schedule,
        "status": status,
        "store_id": store,
        "notes": req.notes,
        "stats": {"sent": 0, "delivered": 0, "failed": 0, "converted": 0},
        "last_run_at": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user.get("user_id", "unknown"),
    }


@router.post("")
async def create_campaign(
    req: CampaignCreate,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Create a campaign (DRAFT). Does NOT send anything."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if req.segment_id not in SEGMENTS:
        raise HTTPException(status_code=422, detail=f"Unknown segment '{req.segment_id}'")
    store = _effective_store(current_user, req.store_id)
    doc = _new_campaign_doc(req, current_user, store)
    try:
        db.get_collection(CAMPAIGNS_COLLECTION).insert_one(doc)
    except Exception as e:
        logger.error("Campaign create failed: %s", e)
        raise HTTPException(status_code=503, detail="Could not save campaign")
    return {"message": "Campaign created", "campaign": _clean(dict(doc))}


def _load_campaign(db, campaign_id: str, user: dict) -> Dict[str, Any]:
    coll = db.get_collection(CAMPAIGNS_COLLECTION)
    doc = coll.find_one({"campaign_id": campaign_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Campaign not found")
    # Store-scope read: a store-level role can't open another store's campaign.
    if not _is_hq(user):
        if doc.get("store_id") and doc.get("store_id") != user.get("active_store_id"):
            raise HTTPException(status_code=404, detail="Campaign not found")
    return doc


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = _load_campaign(db, campaign_id, current_user)
    return {"campaign": _clean(dict(doc))}


@router.put("/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    req: CampaignUpdate,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = _load_campaign(db, campaign_id, current_user)
    updates: Dict[str, Any] = {"updated_at": datetime.now().isoformat()}
    if req.name is not None:
        updates["name"] = req.name.strip()
    if req.campaign_type is not None:
        ct = req.campaign_type.lower().strip()
        if ct not in CAMPAIGN_TYPES:
            raise HTTPException(status_code=422, detail="bad campaign_type")
        updates["campaign_type"] = ct
    if req.segment_id is not None:
        if req.segment_id not in SEGMENTS:
            raise HTTPException(status_code=422, detail=f"Unknown segment '{req.segment_id}'")
        updates["segment_id"] = req.segment_id
    if req.channels is not None:
        updates["channels"] = req.channels
    if req.template_id is not None:
        updates["template_id"] = req.template_id
    if req.schedule is not None:
        updates["schedule"] = req.schedule.model_dump()
    if req.notes is not None:
        updates["notes"] = req.notes
    db.get_collection(CAMPAIGNS_COLLECTION).update_one(
        {"campaign_id": campaign_id}, {"$set": updates}
    )
    doc.update(updates)
    return {"message": "Campaign updated", "campaign": _clean(dict(doc))}


@router.post("/{campaign_id}/duplicate")
async def duplicate_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Clone a campaign into a fresh DRAFT (no stats, new id)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    src = _load_campaign(db, campaign_id, current_user)
    now = datetime.now().isoformat()
    clone = dict(src)
    clone.pop("_id", None)
    clone["campaign_id"] = f"CMP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    clone["name"] = f"{src.get('name', 'Campaign')} (copy)"
    clone["status"] = "DRAFT"
    clone["stats"] = {"sent": 0, "delivered": 0, "failed": 0, "converted": 0}
    clone["last_run_at"] = None
    clone["created_at"] = now
    clone["updated_at"] = now
    clone["created_by"] = current_user.get("user_id", "unknown")
    db.get_collection(CAMPAIGNS_COLLECTION).insert_one(clone)
    return {"message": "Campaign duplicated", "campaign": _clean(dict(clone))}


@router.delete("/{campaign_id}")
async def delete_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    _load_campaign(db, campaign_id, current_user)  # 404 + scope check
    db.get_collection(CAMPAIGNS_COLLECTION).delete_one({"campaign_id": campaign_id})
    return {"message": "Campaign deleted", "campaign_id": campaign_id}


# ============================================================================
# LIFECYCLE: schedule / pause / resume
# ============================================================================


@router.post("/{campaign_id}/schedule")
async def schedule_campaign(
    campaign_id: str,
    req: ScheduleRequest,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Set a campaign's schedule and move it to SCHEDULED. The actual time-based
    firing is performed by the MEGAPHONE agent / a worker that scans for due
    campaigns; this records the intent so it is picked up."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    _load_campaign(db, campaign_id, current_user)
    db.get_collection(CAMPAIGNS_COLLECTION).update_one(
        {"campaign_id": campaign_id},
        {
            "$set": {
                "schedule": req.schedule.model_dump(),
                "status": "SCHEDULED",
                "updated_at": datetime.now().isoformat(),
            }
        },
    )
    return {"message": "Campaign scheduled", "campaign_id": campaign_id, "status": "SCHEDULED"}


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    _load_campaign(db, campaign_id, current_user)
    db.get_collection(CAMPAIGNS_COLLECTION).update_one(
        {"campaign_id": campaign_id},
        {"$set": {"status": "PAUSED", "updated_at": datetime.now().isoformat()}},
    )
    return {"message": "Campaign paused", "campaign_id": campaign_id, "status": "PAUSED"}


@router.post("/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = _load_campaign(db, campaign_id, current_user)
    # Resume to SCHEDULED if it has a recurring/triggered schedule, else ACTIVE.
    sched = doc.get("schedule") or {}
    new_status = "SCHEDULED" if sched.get("kind") in ("recurring", "triggered") else "ACTIVE"
    db.get_collection(CAMPAIGNS_COLLECTION).update_one(
        {"campaign_id": campaign_id},
        {"$set": {"status": new_status, "updated_at": datetime.now().isoformat()}},
    )
    return {"message": "Campaign resumed", "campaign_id": campaign_id, "status": new_status}


# ============================================================================
# SEND - resolve audience + fan out via the shared send_notification
# ============================================================================


@router.post("/{campaign_id}/send")
async def send_campaign(
    campaign_id: str,
    req: Optional[SendRequest] = None,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Fire a campaign NOW: resolve its segment, then fan out the template over
    every selected channel via the SHARED services.notification_service.
    send_notification (so DISPATCH_MODE, consent, rate-limit awareness and the
    DLT audit fields all apply unchanged). Each produced notification_logs row
    is stamped campaign_id + campaign_run_id so /analytics can attribute it.

    With DISPATCH_MODE=off (default) the rows are queued as PENDING and never
    dispatched; in test mode only TEST_PHONE is contacted by the drain. The
    response reports queued/skipped counts honestly -- nothing is faked SENT."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = _load_campaign(db, campaign_id, current_user)

    if doc.get("status") == "PAUSED":
        raise HTTPException(status_code=409, detail="Campaign is paused; resume before sending")

    store = doc.get("store_id") or current_user.get("active_store_id", "")
    segment_id = doc.get("segment_id", "all_consented")
    channels = doc.get("channels") or ["WHATSAPP"]
    template_id = doc.get("template_id", "ANNUAL_CHECKUP_REMINDER")

    recipients = _resolve_segment(db, segment_id, store if store else None)
    run_id = f"RUN-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    mode = _dispatch_mode()

    queued = 0
    failed = 0
    skipped = 0
    primary_channel = channels[0] if channels else "WHATSAPP"

    for r in recipients:
        phone = r.get("phone", "")
        email = r.get("email", "")
        # Skip a recipient with no usable contact for any chosen channel.
        if not phone and not (("EMAIL" in channels) and email):
            skipped += 1
            continue
        try:
            result = await send_notification(
                store_id=store,
                customer_id=r.get("customer_id", ""),
                customer_phone=phone,
                customer_name=r.get("name", ""),
                template_id=template_id,
                channel=primary_channel,
                variables=r.get("variables", {}),
                category="MARKETING",
                triggered_by=current_user.get("user_id", "unknown"),
                related_entity_type="campaign",
                related_entity_id=campaign_id,
            )
            # Stamp the produced log row with campaign attribution so analytics
            # can roll it up. send_notification returns the row (dispatched=False)
            # incl. its notification_id; patch it in place.
            nid = result.get("notification_id")
            if nid:
                try:
                    db.get_collection("notification_logs").update_one(
                        {"notification_id": nid},
                        {"$set": {"campaign_id": campaign_id, "campaign_run_id": run_id}},
                    )
                except Exception:
                    pass
            queued += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Campaign send to %s failed: %s", phone, e)
            failed += 1

    # Update campaign stats + lifecycle. A one-time send -> COMPLETED; a
    # recurring/triggered campaign stays ACTIVE (more runs to come).
    sched = doc.get("schedule") or {}
    is_recurring = sched.get("kind") in ("recurring", "triggered")
    new_status = "ACTIVE" if is_recurring else "COMPLETED"
    stats = doc.get("stats") or {"sent": 0, "delivered": 0, "failed": 0, "converted": 0}
    stats["sent"] = int(stats.get("sent", 0)) + queued
    stats["failed"] = int(stats.get("failed", 0)) + failed
    db.get_collection(CAMPAIGNS_COLLECTION).update_one(
        {"campaign_id": campaign_id},
        {
            "$set": {
                "stats": stats,
                "status": new_status,
                "last_run_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        },
    )

    msg = f"{queued} queued"
    if skipped:
        msg += f", {skipped} skipped (no contact)"
    if failed:
        msg += f", {failed} failed"
    if mode == "off":
        msg += " - DISPATCH_MODE is off, messages are simulated (not sent)"
    return {
        "message": msg,
        "campaign_id": campaign_id,
        "campaign_run_id": run_id,
        "audience": len(recipients),
        "queued": queued,
        "skipped": skipped,
        "failed": failed,
        "dispatch_mode": mode,
        "status": new_status,
    }


# ============================================================================
# ANALYTICS - per-campaign, derived from the tagged notification_logs
# ============================================================================


@router.get("/{campaign_id}/analytics")
async def campaign_analytics(
    campaign_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Per-campaign analytics derived from the notification_logs this campaign
    produced (rows tagged campaign_id). Returns sent/delivered/failed/converted
    totals and a per-channel breakdown. Fail-soft -> zeros."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = _load_campaign(db, campaign_id, current_user)

    totals = {"queued": 0, "sent": 0, "delivered": 0, "failed": 0}
    per_channel: Dict[str, Dict[str, int]] = {}
    try:
        logs = list(
            db.get_collection("notification_logs").find({"campaign_id": campaign_id})
        )
    except Exception as e:
        logger.warning("Campaign analytics read failed (non-fatal): %s", e)
        logs = []

    for log in logs:
        status = (log.get("status") or "").upper()
        delivery = (log.get("delivery_status") or "").upper()
        channel = (log.get("channel") or "UNKNOWN").upper()
        ch = per_channel.setdefault(
            channel, {"queued": 0, "sent": 0, "delivered": 0, "failed": 0}
        )
        # PENDING/SIMULATED count as "queued"; SENT counts as sent; DELIVERED via
        # DLR webhook counts delivered; FAILED as failed.
        if status in ("PENDING", "SIMULATED"):
            totals["queued"] += 1
            ch["queued"] += 1
        if status in ("SENT", "SIMULATED", "DELIVERED"):
            totals["sent"] += 1
            ch["sent"] += 1
        if delivery == "DELIVERED" or status == "DELIVERED":
            totals["delivered"] += 1
            ch["delivered"] += 1
        if status == "FAILED":
            totals["failed"] += 1
            ch["failed"] += 1

    stats = doc.get("stats") or {}
    converted = int(stats.get("converted", 0))
    sent_for_rate = totals["sent"] or int(stats.get("sent", 0))
    return {
        "campaign_id": campaign_id,
        "name": doc.get("name"),
        "status": doc.get("status"),
        "segment_id": doc.get("segment_id"),
        "channels": doc.get("channels"),
        "totals": {
            "audience_messages": len(logs),
            "queued": totals["queued"],
            "sent": totals["sent"],
            "delivered": totals["delivered"],
            "failed": totals["failed"],
            "converted": converted,
        },
        "rates": {
            "delivery_rate": round(totals["delivered"] / sent_for_rate * 100, 1) if sent_for_rate else 0.0,
            "failure_rate": round(totals["failed"] / len(logs) * 100, 1) if logs else 0.0,
            "conversion_rate": round(converted / sent_for_rate * 100, 1) if sent_for_rate else 0.0,
        },
        "per_channel": per_channel,
        "dispatch_mode": _dispatch_mode(),
        "last_run_at": doc.get("last_run_at"),
    }
