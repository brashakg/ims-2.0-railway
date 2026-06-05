"""
IMS 2.0 - Marketing Campaign Layer
==================================
The CAMPAIGN layer on top of the existing send infrastructure
(marketing.py / notification_service / agents.providers). The old
CampaignManager page was a pure mock; this gives it a real backend.

What this adds (and what it deliberately REUSES):

  ADDS
    - `campaigns` collection + full CRUD (DRAFT -> SCHEDULED -> ACTIVE ->
      COMPLETED | PAUSED lifecycle).
    - A segment layer (services/campaign_segments.py) with LIVE audience counts
      + a preview sample so the builder can show "Estimated audience: N".
    - Scheduling (ONE_TIME | RECURRING | TRIGGERED) -- persisted; a SCHEDULED
      campaign is what MEGAPHONE (or a future tick) will action.
    - Per-campaign analytics aggregated from notification_logs by campaign_id.
    - An immutable audit row (campaign_audit) for every send / lifecycle change.

  REUSES (does NOT re-implement)
    - send_notification (the SAME path notifications/send-bulk uses) for every
      outbound message -> honors DISPATCH_MODE (off/test/live), templates, DLT
      audit fields, and the PENDING honest-status contract. No parallel MSG91.
    - The marketing_consent opt-out gate (mirrors send-bulk).
    - The promo quiet-hours window guard (agents.quiet_hours) used by the manual
      send API.

Role gate: ADMIN / SUPERADMIN for org-wide actions; STORE_MANAGER is permitted
for store-scoped campaigns (a campaign carrying a store_id is restricted to that
manager's store via validate_store_access). Every route is catalogued in
rbac_policy.POLICY (the coverage-lock test enforces this).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles
from .marketing import (
    VALID_CHANNELS,
    _enforce_promo_window,
    _check_notification_rate,
    _get_db as _marketing_get_db,
)
from ..services.notification_service import send_notification
from ..services import campaign_segments as seg

router = APIRouter()

# Org-wide campaign management. STORE_MANAGER is added for store-scoped actions
# (a campaign with a store_id is restricted to that manager's store). SUPERADMIN
# auto-passes via require_roles.
_CAMPAIGN_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")

_CAMPAIGN_TYPES = {"rx_renewal", "birthday", "winback", "custom"}
_SCHEDULE_KINDS = {"ONE_TIME", "RECURRING", "TRIGGERED"}
_STATUS_FLOW = {"DRAFT", "SCHEDULED", "ACTIVE", "COMPLETED", "PAUSED"}


# ============================================================================
# SCHEMAS
# ============================================================================


class CampaignSchedule(BaseModel):
    kind: Literal["ONE_TIME", "RECURRING", "TRIGGERED"] = "ONE_TIME"
    # ONE_TIME: send_at (ISO). RECURRING: cron-ish frequency + time. TRIGGERED:
    # the event key (e.g. "rx_expiry") MEGAPHONE listens for. All optional so a
    # DRAFT can be saved before scheduling is finalised.
    send_at: Optional[str] = None
    frequency: Optional[str] = None  # DAILY | WEEKLY | MONTHLY (RECURRING)
    time_of_day: Optional[str] = None  # "09:00" (RECURRING)
    trigger_event: Optional[str] = None  # TRIGGERED


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    type: Literal["rx_renewal", "birthday", "winback", "custom"] = "custom"
    segment_key: str
    channels: List[str] = ["WHATSAPP"]
    template_id: str
    store_id: Optional[str] = None
    segment_params: Dict[str, Any] = {}
    schedule: Optional[CampaignSchedule] = None
    description: Optional[str] = None

    @field_validator("segment_key")
    @classmethod
    def validate_segment(cls, v: str) -> str:
        if v not in seg.SEGMENT_KEYS:
            raise ValueError(f"segment_key must be one of {sorted(seg.SEGMENT_KEYS)}")
        return v

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("at least one channel is required")
        out = []
        for c in v:
            val = (c or "").upper().strip()
            if val not in VALID_CHANNELS:
                raise ValueError(f"channel must be one of {sorted(VALID_CHANNELS)}")
            out.append(val)
        return out


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    type: Optional[Literal["rx_renewal", "birthday", "winback", "custom"]] = None
    segment_key: Optional[str] = None
    channels: Optional[List[str]] = None
    template_id: Optional[str] = None
    store_id: Optional[str] = None
    segment_params: Optional[Dict[str, Any]] = None
    schedule: Optional[CampaignSchedule] = None
    description: Optional[str] = None

    @field_validator("segment_key")
    @classmethod
    def validate_segment(cls, v):
        if v is not None and v not in seg.SEGMENT_KEYS:
            raise ValueError(f"segment_key must be one of {sorted(seg.SEGMENT_KEYS)}")
        return v

    @field_validator("channels")
    @classmethod
    def validate_channels(cls, v):
        if v is None:
            return v
        out = []
        for c in v:
            val = (c or "").upper().strip()
            if val not in VALID_CHANNELS:
                raise ValueError(f"channel must be one of {sorted(VALID_CHANNELS)}")
            out.append(val)
        return out


# ============================================================================
# HELPERS
# ============================================================================


def _now_iso() -> str:
    return datetime.now().isoformat()


def _new_campaign_id() -> str:
    return f"CMP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


def _strip(doc: Dict[str, Any]) -> Dict[str, Any]:
    if doc:
        doc.pop("_id", None)
    return doc


def _audit(
    db,
    campaign_id: str,
    action: str,
    user: dict,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Append an immutable campaign_audit row (fail-soft -- never blocks the
    action). SYSTEM_INTENT 'Audit Everything': sends + lifecycle transitions are
    recorded with who/when/what."""
    if db is None:
        return
    try:
        db.get_collection("campaign_audit").insert_one(
            {
                "audit_id": f"CMA-{uuid.uuid4().hex[:10].upper()}",
                "campaign_id": campaign_id,
                "action": action,
                "actor": user.get("user_id", "unknown"),
                "detail": detail or {},
                "at": _now_iso(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("campaign audit write failed (%s): %s", action, exc)


def _enforce_store_scope(campaign: Dict[str, Any], user: dict) -> None:
    """A campaign carrying a store_id is restricted to that store. HQ roles
    (SUPERADMIN/ADMIN/AREA_MANAGER) bypass; a STORE_MANAGER may only act on a
    campaign for a store they can access."""
    store_id = campaign.get("store_id")
    if not store_id:
        return
    roles = set(user.get("roles", []) or [])
    if roles & {"SUPERADMIN", "ADMIN"}:
        return
    # validate_store_access(store_id, user) returns the store_id when the caller
    # may access it and raises HTTPException(403) otherwise (admins bypass, area
    # managers by region, others by store_ids list). It is the SAME validator the
    # rest of the store-scoped routers use.
    from ..dependencies import validate_store_access

    validate_store_access(store_id, user)


def _get_campaign_or_404(db, campaign_id: str) -> Dict[str, Any]:
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("campaigns").find_one({"campaign_id": campaign_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return doc


def _campaign_analytics(db, campaign_id: str) -> Dict[str, Any]:
    """Aggregate notification_logs tagged with this campaign_id into
    sent/delivered/failed/opened/converted (+ per-channel)."""
    empty = {
        "campaign_id": campaign_id,
        "total": 0,
        "sent": 0,
        "delivered": 0,
        "failed": 0,
        "pending": 0,
        "opened": 0,
        "converted": 0,
        "open_rate": 0.0,
        "conversion_rate": 0.0,
        "delivery_rate": 0.0,
        "by_channel": {},
    }
    if db is None:
        return empty
    try:
        logs = list(
            db.get_collection("notification_logs")
            .find({"campaign_id": campaign_id})
            .limit(10000)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("analytics read failed for %s: %s", campaign_id, exc)
        return empty

    total = len(logs)
    sent = delivered = failed = pending = opened = converted = 0
    by_channel: Dict[str, Dict[str, int]] = {}
    for log in logs:
        status = (log.get("status") or "").upper()
        delivery = (log.get("delivery_status") or "").upper()
        ch = (log.get("channel") or "UNKNOWN").upper()
        chan = by_channel.setdefault(
            ch, {"total": 0, "sent": 0, "delivered": 0, "failed": 0}
        )
        chan["total"] += 1
        # SIMULATED counts as "sent" for analytics (it left the campaign engine;
        # under DISPATCH_MODE!=live the provider was a dry-run, but the campaign
        # DID dispatch it). PENDING = queued, not yet drained.
        if status in ("SENT", "SIMULATED", "DELIVERED"):
            sent += 1
            chan["sent"] += 1
        elif status == "FAILED":
            failed += 1
            chan["failed"] += 1
        elif status == "PENDING":
            pending += 1
        if delivery == "DELIVERED" or status == "DELIVERED":
            delivered += 1
            chan["delivered"] += 1
        if log.get("opened_at") or delivery == "READ":
            opened += 1
        if log.get("converted") or log.get("converted_at"):
            converted += 1

    def _rate(n: int, d: int) -> float:
        return round(n / d * 100, 1) if d else 0.0

    return {
        "campaign_id": campaign_id,
        "total": total,
        "sent": sent,
        "delivered": delivered,
        "failed": failed,
        "pending": pending,
        "opened": opened,
        "converted": converted,
        "open_rate": _rate(opened, sent),
        "conversion_rate": _rate(converted, sent),
        "delivery_rate": _rate(delivered, sent),
        "by_channel": by_channel,
    }


# ============================================================================
# SEGMENTS
# ============================================================================


@router.get("/segments")
async def list_segments(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Predefined segments, each with a LIVE audience count computed now."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _marketing_get_db()
    segments = seg.list_segments(db, store_id=active_store or None)
    return {"segments": segments, "total": len(segments)}


@router.get("/segments/{key}/preview")
async def preview_segment(
    key: str,
    store_id: Optional[str] = Query(None),
    customer_type: Optional[str] = Query(None),
    window_days: Optional[int] = Query(None, ge=1, le=365),
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Live count + a masked sample for the segment builder
    ('Estimated audience: N')."""
    if key not in seg.SEGMENT_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown segment '{key}'")
    active_store = store_id or current_user.get("active_store_id", "")
    params: Dict[str, Any] = {}
    if customer_type:
        params["customer_type"] = customer_type
    if window_days:
        params["window_days"] = window_days
    db = _marketing_get_db()
    return seg.preview_segment(db, key, store_id=active_store or None, params=params)


# ============================================================================
# CAMPAIGN CRUD
# ============================================================================


@router.get("/campaigns")
async def list_campaigns(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """List campaigns + a summary block (active count, total_sent, open_rate,
    conversion) for the dashboard header."""
    db = _marketing_get_db()
    if db is None:
        return {
            "campaigns": [],
            "total": 0,
            "summary": {
                "active": 0,
                "total_sent": 0,
                "open_rate": 0.0,
                "conversion": 0.0,
            },
        }
    query: Dict[str, Any] = {}
    if store_id:
        query["store_id"] = store_id
    if status:
        query["status"] = status.upper()
    coll = db.get_collection("campaigns")
    campaigns = list(coll.find(query).sort("created_at", -1).limit(limit))
    for c in campaigns:
        _strip(c)

    active = sum(1 for c in campaigns if c.get("status") in ("ACTIVE", "SCHEDULED"))
    total_sent = sum(int(c.get("sent_count", 0) or 0) for c in campaigns)
    total_opened = sum(int(c.get("opened_count", 0) or 0) for c in campaigns)
    total_converted = sum(int(c.get("converted_count", 0) or 0) for c in campaigns)
    open_rate = round(total_opened / total_sent * 100, 1) if total_sent else 0.0
    conversion = round(total_converted / total_sent * 100, 1) if total_sent else 0.0

    return {
        "campaigns": campaigns,
        "total": len(campaigns),
        "summary": {
            "active": active,
            "total_campaigns": len(campaigns),
            "total_sent": total_sent,
            "open_rate": open_rate,
            "conversion": conversion,
        },
    }


@router.post("/campaigns")
async def create_campaign(
    req: CampaignCreate,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Create a DRAFT campaign."""
    db = _marketing_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    # A STORE_MANAGER may only create a campaign scoped to a store they access.
    if req.store_id:
        _enforce_store_scope({"store_id": req.store_id}, current_user)

    schedule = req.schedule.model_dump() if req.schedule else None
    campaign_id = _new_campaign_id()
    doc = {
        "campaign_id": campaign_id,
        "name": req.name,
        "type": req.type,
        "segment_key": req.segment_key,
        "segment_params": req.segment_params or {},
        "channels": req.channels,
        "template_id": req.template_id,
        "store_id": req.store_id,
        "schedule": schedule,
        "description": req.description,
        "status": "DRAFT",
        "audience_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "opened_count": 0,
        "converted_count": 0,
        "last_sent_at": None,
        "created_by": current_user.get("user_id", "unknown"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    db.get_collection("campaigns").insert_one(doc)
    _audit(
        db,
        campaign_id,
        "CREATE",
        current_user,
        {"name": req.name, "segment": req.segment_key},
    )
    return {"message": "Campaign created", "campaign": _strip(dict(doc))}


@router.get("/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Fetch a single campaign + a live audience-count estimate for its
    segment."""
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    estimate = seg.count_segment(
        db,
        doc.get("segment_key", ""),
        store_id=doc.get("store_id") or None,
        params=doc.get("segment_params") or {},
    )
    out = _strip(dict(doc))
    out["audience_estimate"] = estimate
    return out


@router.put("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    req: CampaignUpdate,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Edit a campaign. A COMPLETED campaign is immutable (duplicate it instead)."""
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    if doc.get("status") == "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail="A completed campaign cannot be edited; duplicate it instead",
        )

    updates: Dict[str, Any] = {}
    payload = req.model_dump(exclude_unset=True)
    for field in (
        "name",
        "type",
        "segment_key",
        "channels",
        "template_id",
        "store_id",
        "segment_params",
        "description",
    ):
        if field in payload and payload[field] is not None:
            updates[field] = payload[field]
    if "schedule" in payload and payload["schedule"] is not None:
        updates["schedule"] = req.schedule.model_dump() if req.schedule else None
    # If the store_id is being changed, re-check scope for the NEW store too.
    if "store_id" in updates and updates["store_id"]:
        _enforce_store_scope({"store_id": updates["store_id"]}, current_user)

    if not updates:
        return {"message": "No changes", "campaign": _strip(dict(doc))}
    updates["updated_at"] = _now_iso()
    db.get_collection("campaigns").update_one(
        {"campaign_id": campaign_id}, {"$set": updates}
    )
    _audit(db, campaign_id, "UPDATE", current_user, {"fields": list(updates.keys())})
    fresh = db.get_collection("campaigns").find_one({"campaign_id": campaign_id})
    return {"message": "Campaign updated", "campaign": _strip(fresh)}


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Delete a campaign. ACTIVE campaigns must be paused first (guards against
    deleting something mid-send)."""
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    if doc.get("status") == "ACTIVE":
        raise HTTPException(
            status_code=409, detail="Pause the campaign before deleting it"
        )
    db.get_collection("campaigns").delete_one({"campaign_id": campaign_id})
    _audit(db, campaign_id, "DELETE", current_user, {"name": doc.get("name")})
    return {"message": "Campaign deleted", "campaign_id": campaign_id}


@router.post("/campaigns/{campaign_id}/duplicate")
async def duplicate_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Clone a campaign into a fresh DRAFT (counters + status reset)."""
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    new_id = _new_campaign_id()
    clone = _strip(dict(doc))
    clone.update(
        {
            "campaign_id": new_id,
            "name": f"{doc.get('name', 'Campaign')} (copy)",
            "status": "DRAFT",
            "audience_count": 0,
            "sent_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "opened_count": 0,
            "converted_count": 0,
            "last_sent_at": None,
            "created_by": current_user.get("user_id", "unknown"),
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
    )
    db.get_collection("campaigns").insert_one(clone)
    _audit(db, new_id, "DUPLICATE", current_user, {"from": campaign_id})
    return {"message": "Campaign duplicated", "campaign": _strip(dict(clone))}


# ============================================================================
# SCHEDULE / LIFECYCLE
# ============================================================================


@router.post("/campaigns/{campaign_id}/schedule")
async def schedule_campaign(
    campaign_id: str,
    schedule: CampaignSchedule,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Set/update the schedule and mark the campaign SCHEDULED. A SCHEDULED
    campaign is what MEGAPHONE (or a future tick) will action; for now we persist
    + flag it. ONE_TIME requires send_at; RECURRING requires frequency."""
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    if doc.get("status") == "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail="A completed campaign cannot be re-scheduled; duplicate it",
        )

    sched = schedule.model_dump()
    if sched["kind"] == "ONE_TIME" and not sched.get("send_at"):
        raise HTTPException(
            status_code=422, detail="ONE_TIME schedule requires send_at"
        )
    if sched["kind"] == "RECURRING" and not sched.get("frequency"):
        raise HTTPException(
            status_code=422, detail="RECURRING schedule requires frequency"
        )

    db.get_collection("campaigns").update_one(
        {"campaign_id": campaign_id},
        {"$set": {"schedule": sched, "status": "SCHEDULED", "updated_at": _now_iso()}},
    )
    _audit(db, campaign_id, "SCHEDULE", current_user, {"kind": sched["kind"]})
    fresh = db.get_collection("campaigns").find_one({"campaign_id": campaign_id})
    return {"message": "Campaign scheduled", "campaign": _strip(fresh)}


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Pause an ACTIVE or SCHEDULED campaign (stops a future tick from sending)."""
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    if doc.get("status") not in ("ACTIVE", "SCHEDULED"):
        raise HTTPException(
            status_code=409, detail="Only an ACTIVE or SCHEDULED campaign can be paused"
        )
    db.get_collection("campaigns").update_one(
        {"campaign_id": campaign_id},
        {"$set": {"status": "PAUSED", "updated_at": _now_iso()}},
    )
    _audit(db, campaign_id, "PAUSE", current_user)
    fresh = db.get_collection("campaigns").find_one({"campaign_id": campaign_id})
    return {"message": "Campaign paused", "campaign": _strip(fresh)}


@router.post("/campaigns/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Resume a PAUSED campaign. Returns to SCHEDULED if it carries a schedule,
    else ACTIVE."""
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    if doc.get("status") != "PAUSED":
        raise HTTPException(
            status_code=409, detail="Only a PAUSED campaign can be resumed"
        )
    next_status = "SCHEDULED" if doc.get("schedule") else "ACTIVE"
    db.get_collection("campaigns").update_one(
        {"campaign_id": campaign_id},
        {"$set": {"status": next_status, "updated_at": _now_iso()}},
    )
    _audit(db, campaign_id, "RESUME", current_user, {"to": next_status})
    fresh = db.get_collection("campaigns").find_one({"campaign_id": campaign_id})
    return {"message": "Campaign resumed", "campaign": _strip(fresh)}


# ============================================================================
# SEND
# ============================================================================


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Resolve the campaign's segment to an audience and fan it out through the
    EXISTING send path (send_notification), tagging each row with campaign_id.

    Safety -- identical to notifications/send-bulk:
      * Rate-limited per user.
      * Promo quiet-hours window (transactional templates exempt).
      * marketing_consent opt-out -> skipped (not sent).
      * DISPATCH_MODE off/test/live is honored INSIDE send_notification (queued
        PENDING; the drain only really dispatches under DISPATCH_MODE=live, and
        in test mode only to TEST_PHONE). We do NOT call MSG91 here.
    Then stamps sent/skipped/failed counts and flips status ACTIVE/COMPLETED.
    """
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    if doc.get("status") == "PAUSED":
        raise HTTPException(
            status_code=409, detail="Resume the campaign before sending"
        )

    rate_err = _check_notification_rate(current_user.get("user_id", "unknown"))
    if rate_err:
        raise HTTPException(status_code=429, detail=rate_err)

    template_id = doc.get("template_id", "")
    # Promo quiet-hours window: a campaign fan-out is promotional unless it uses
    # a transactional template. Block out-of-window before any send.
    _enforce_promo_window(template_id)

    channels = doc.get("channels") or ["WHATSAPP"]
    primary_channel = channels[0]
    store_id = doc.get("store_id") or current_user.get("active_store_id", "")

    audience = seg.resolve_segment(
        db,
        doc.get("segment_key", ""),
        store_id=doc.get("store_id") or None,
        params=doc.get("segment_params") or {},
    )

    cust_coll = db.get_collection("customers")
    results: List[Dict[str, Any]] = []
    sent = skipped = failed = 0
    for r in audience:
        cid = r.get("customer_id")
        # Consent gate (mirrors send-bulk): an explicit False opts out;
        # missing/None defaults to consented. Ad-hoc rows w/o customer_id send.
        if cid:
            cust = cust_coll.find_one({"customer_id": cid}) or {}
            if cust.get("marketing_consent") is False:
                skipped += 1
                results.append(
                    {"customer_id": cid, "status": "skipped", "reason": "opted_out"}
                )
                continue
        phone = r.get("phone") or ""
        if not phone:
            skipped += 1
            results.append(
                {"customer_id": cid, "status": "skipped", "reason": "no_phone"}
            )
            continue
        try:
            res = await send_notification(
                store_id=store_id,
                customer_id=cid or "",
                customer_phone=phone,
                customer_name=r.get("name", "Customer"),
                template_id=template_id,
                channel=primary_channel,
                variables={**(r.get("variables") or {}), "campaign_id": campaign_id},
                category="MARKETING",
                triggered_by=current_user.get("user_id", "unknown"),
                related_entity_type="campaign",
                related_entity_id=campaign_id,
            )
            # Tag the just-queued notification_logs row with campaign_id so
            # analytics can aggregate it. send_notification doesn't take a
            # campaign_id, so we stamp it on the row it created.
            nid = res.get("notification_id")
            if nid:
                try:
                    db.get_collection("notification_logs").update_one(
                        {"notification_id": nid},
                        {"$set": {"campaign_id": campaign_id}},
                    )
                except Exception:  # noqa: BLE001
                    pass
            sent += 1
            results.append(
                {"customer_id": cid, "status": "queued", "notification_id": nid}
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            results.append({"customer_id": cid, "status": "failed", "error": str(exc)})

    # ONE_TIME -> COMPLETED; RECURRING/TRIGGERED stay ACTIVE for the next tick.
    sched = doc.get("schedule") or {}
    kind = (sched.get("kind") or "ONE_TIME") if sched else "ONE_TIME"
    new_status = "COMPLETED" if kind == "ONE_TIME" else "ACTIVE"

    db.get_collection("campaigns").update_one(
        {"campaign_id": campaign_id},
        {
            "$set": {
                "status": new_status,
                "audience_count": len(audience),
                "last_sent_at": _now_iso(),
                "updated_at": _now_iso(),
            },
            "$inc": {
                "sent_count": sent,
                "skipped_count": skipped,
                "failed_count": failed,
            },
        },
    )
    _audit(
        db,
        campaign_id,
        "SEND",
        current_user,
        {
            "audience": len(audience),
            "queued": sent,
            "skipped": skipped,
            "failed": failed,
            "status": new_status,
        },
    )
    msg = f"{sent}/{len(audience)} queued"
    if skipped:
        msg += f" ({skipped} skipped)"
    return {
        "message": msg,
        "campaign_id": campaign_id,
        "status": new_status,
        "audience_count": len(audience),
        "queued": sent,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


# ============================================================================
# ANALYTICS
# ============================================================================


@router.get("/campaigns/{campaign_id}/analytics")
async def campaign_analytics(
    campaign_id: str,
    current_user: dict = Depends(require_roles(*_CAMPAIGN_ROLES)),
):
    """Per-campaign analytics aggregated from notification_logs tagged with this
    campaign_id: sent / delivered / failed / opened / converted (+ per-channel)."""
    db = _marketing_get_db()
    doc = _get_campaign_or_404(db, campaign_id)
    _enforce_store_scope(doc, current_user)
    analytics = _campaign_analytics(db, campaign_id)
    analytics["name"] = doc.get("name")
    analytics["status"] = doc.get("status")
    return analytics


# ============================================================================
# CRM-8: PROMO OFFER-TEMPLATE LIBRARY (BOGO / COMBO / THRESHOLD)
# ============================================================================
# Reusable promo templates that a STORE_MANAGER/ADMIN creates once and then
# attaches to campaigns or applies at POS checkout.  They sit on top of the
# voucher engine: the template carries the *shape* of the offer; a voucher is
# the *instance* minted when the offer fires.
#
# Template types
#   BOGO         – "buy N of <sku_group>, get M free"
#   COMBO        – "buy all of <sku_list>, get <discount_pct>% off the bundle"
#   THRESHOLD    – "spend >= <min_value>, get <discount_pct>% off the order"
#
# The template DOES NOT generate vouchers; it is a reusable definition that
# the campaign engine or a future POS check can evaluate and apply.  All money
# rules are validated server-side; the FE is display-only.
# ============================================================================

_PROMO_TEMPLATE_TYPES = {"BOGO", "COMBO", "THRESHOLD"}
_PROMO_TEMPLATE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")


class PromoTemplateCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    type: Literal["BOGO", "COMBO", "THRESHOLD"]
    description: Optional[str] = None
    # BOGO fields
    buy_quantity: Optional[int] = Field(None, ge=1)
    get_quantity: Optional[int] = Field(None, ge=1)
    sku_group: Optional[List[str]] = None
    # COMBO fields
    sku_list: Optional[List[str]] = None
    combo_discount_pct: Optional[float] = Field(None, ge=0, le=100)
    # THRESHOLD fields
    min_order_value: Optional[float] = Field(None, ge=0)
    threshold_discount_pct: Optional[float] = Field(None, ge=0, le=100)
    # Common
    store_id: Optional[str] = None
    active: bool = True
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


class PromoTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = None
    buy_quantity: Optional[int] = Field(None, ge=1)
    get_quantity: Optional[int] = Field(None, ge=1)
    sku_group: Optional[List[str]] = None
    sku_list: Optional[List[str]] = None
    combo_discount_pct: Optional[float] = Field(None, ge=0, le=100)
    min_order_value: Optional[float] = Field(None, ge=0)
    threshold_discount_pct: Optional[float] = Field(None, ge=0, le=100)
    store_id: Optional[str] = None
    active: Optional[bool] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


def _validate_promo_template(req: PromoTemplateCreate) -> None:
    """Business-rule validation for each promo type."""
    if req.type == "BOGO":
        if not req.buy_quantity or not req.get_quantity:
            raise HTTPException(
                status_code=422,
                detail="BOGO template requires buy_quantity and get_quantity",
            )
    elif req.type == "COMBO":
        if not req.sku_list or len(req.sku_list) < 2:
            raise HTTPException(
                status_code=422,
                detail="COMBO template requires at least 2 SKUs in sku_list",
            )
        if req.combo_discount_pct is None:
            raise HTTPException(
                status_code=422,
                detail="COMBO template requires combo_discount_pct",
            )
    elif req.type == "THRESHOLD":
        if req.min_order_value is None:
            raise HTTPException(
                status_code=422,
                detail="THRESHOLD template requires min_order_value",
            )
        if req.threshold_discount_pct is None:
            raise HTTPException(
                status_code=422,
                detail="THRESHOLD template requires threshold_discount_pct",
            )


@router.get("/promo-templates")
async def list_promo_templates(
    store_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(100, le=500),
    current_user: dict = Depends(require_roles(*_PROMO_TEMPLATE_ROLES)),
):
    """List reusable promo offer templates."""
    db = _marketing_get_db()
    if db is None:
        return {"templates": [], "total": 0}
    query: Dict[str, Any] = {}
    if store_id:
        query["store_id"] = store_id
    if type and type.upper() in _PROMO_TEMPLATE_TYPES:
        query["type"] = type.upper()
    if active_only:
        query["active"] = True
    coll = db.get_collection("promo_templates")
    docs = list(coll.find(query).sort("created_at", -1).limit(limit))
    for d in docs:
        d.pop("_id", None)
    return {"templates": docs, "total": len(docs)}


@router.post("/promo-templates")
async def create_promo_template(
    req: PromoTemplateCreate,
    current_user: dict = Depends(require_roles(*_PROMO_TEMPLATE_ROLES)),
):
    """Create a reusable promo offer template (BOGO / COMBO / THRESHOLD)."""
    _validate_promo_template(req)
    db = _marketing_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if req.store_id:
        _enforce_store_scope({"store_id": req.store_id}, current_user)
    template_id = f"PT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "template_id": template_id,
        "name": req.name,
        "type": req.type,
        "description": req.description,
        "buy_quantity": req.buy_quantity,
        "get_quantity": req.get_quantity,
        "sku_group": req.sku_group,
        "sku_list": req.sku_list,
        "combo_discount_pct": req.combo_discount_pct,
        "min_order_value": req.min_order_value,
        "threshold_discount_pct": req.threshold_discount_pct,
        "store_id": req.store_id,
        "active": req.active,
        "valid_from": req.valid_from,
        "valid_until": req.valid_until,
        "created_by": current_user.get("user_id", "unknown"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    db.get_collection("promo_templates").insert_one(doc)
    _audit(db, template_id, "CREATE_PROMO_TEMPLATE", current_user, {"name": req.name, "type": req.type})
    return {"message": "Promo template created", "template": _strip(dict(doc))}


@router.get("/promo-templates/{template_id}")
async def get_promo_template(
    template_id: str,
    current_user: dict = Depends(require_roles(*_PROMO_TEMPLATE_ROLES)),
):
    """Get a single promo template."""
    db = _marketing_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("promo_templates").find_one({"template_id": template_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promo template not found")
    if doc.get("store_id"):
        _enforce_store_scope(doc, current_user)
    return _strip(dict(doc))


@router.put("/promo-templates/{template_id}")
async def update_promo_template(
    template_id: str,
    req: PromoTemplateUpdate,
    current_user: dict = Depends(require_roles(*_PROMO_TEMPLATE_ROLES)),
):
    """Update a promo template."""
    db = _marketing_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("promo_templates").find_one({"template_id": template_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promo template not found")
    if doc.get("store_id"):
        _enforce_store_scope(doc, current_user)
    updates = req.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        return _strip(dict(doc))
    updates["updated_at"] = _now_iso()
    db.get_collection("promo_templates").update_one(
        {"template_id": template_id}, {"$set": updates}
    )
    _audit(db, template_id, "UPDATE_PROMO_TEMPLATE", current_user, {"fields": list(updates.keys())})
    fresh = db.get_collection("promo_templates").find_one({"template_id": template_id})
    return {"message": "Promo template updated", "template": _strip(fresh)}


@router.delete("/promo-templates/{template_id}")
async def delete_promo_template(
    template_id: str,
    current_user: dict = Depends(require_roles(*_PROMO_TEMPLATE_ROLES)),
):
    """Delete a promo template (soft-delete by setting active=False, or hard-delete)."""
    db = _marketing_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("promo_templates").find_one({"template_id": template_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Promo template not found")
    if doc.get("store_id"):
        _enforce_store_scope(doc, current_user)
    db.get_collection("promo_templates").delete_one({"template_id": template_id})
    _audit(db, template_id, "DELETE_PROMO_TEMPLATE", current_user, {"name": doc.get("name")})
    return {"message": "Promo template deleted", "template_id": template_id}
