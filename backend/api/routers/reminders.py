"""
IMS 2.0 - Reminder rules router (Engine E6, Wave 3)
===================================================
HTTP surface for the config-driven reminder rail. A ``reminder_rules`` document
is the UI-editable config row for one recurring/triggered reminder type. The
engine logic lives in services/reminder_rail.py (gates + cap + send); this router
is CRUD + toggle + preview(dry_run) + run-now + history on top of it.

REUSES (does NOT re-implement):
  - campaigns._enforce_store_scope        -> STORE rules locked to their store.
  - the campaigns _audit writer pattern   -> writes to reminder_audit.
  - reminder_rail.evaluate_rule           -> the one gate/send engine.
  - campaign_segments.SEGMENT_KEYS         -> segment validation.
  - marketing._check_notification_rate    -> run-now rate limit.

BUILD-DARK: nothing here flips DISPATCH_MODE. preview is dry_run (writes nothing).
run-now calls evaluate_rule which rides send_notification (PENDING, off by
default). Seeded rules are active=False so a deploy never auto-sends.

E2 hierarchy (STORE > ENTITY > GLOBAL for the same rule_type) is resolved at
query time -- the same precedence E2's get_policy uses, applied here over the
small reminder_rules set (no separate resolver engine to fork).

No emojis (Windows cp1252). Single-document writes only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

from .auth import require_roles
from .marketing import VALID_CHANNELS, _check_notification_rate, _get_db as _marketing_get_db
from .campaigns import _enforce_store_scope
from ..services import campaign_segments as seg
from ..services import reminder_rail

router = APIRouter()

# Read roles mirror the campaign roles (ADMIN / AREA_MANAGER / STORE_MANAGER;
# SUPERADMIN implicit via require_roles). Writes (create/update/delete/run-now)
# require ADMIN+ for GLOBAL/ENTITY rules; a STORE_MANAGER may act on their own
# store's rules (enforced via _enforce_store_scope inside the handler).
_READ_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")
_WRITE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")

_RULE_TYPES = {
    "rx_expiry",
    "birthday",
    "winback",
    "cl_reorder",
    "churn_risk",
    "lookbook",
    "feedback",
    "fu_due_today",
    "custom",
}
_SCOPES = {"GLOBAL", "ENTITY", "STORE"}
_TRIGGER_KINDS = {"CRON", "EVENT"}

# Counters the engine owns -- not editable through the API.
_PROTECTED_FIELDS = {
    "rule_id",
    "sent_count",
    "skipped_count",
    "failed_count",
    "last_run_at",
    "last_resolved",
    "created_by",
    "created_at",
}


# ============================================================================
# SCHEMAS
# ============================================================================


class TriggerSpec(BaseModel):
    kind: Literal["CRON", "EVENT"] = "CRON"
    cron: Optional[str] = None  # e.g. "DAILY 09:00"
    event_key: Optional[str] = None  # e.g. "churn.detected" (EVENT)


class VoucherTemplate(BaseModel):
    type: Literal["GIFT_CARD", "DISCOUNT"] = "DISCOUNT"
    amount: float = Field(..., ge=0)
    validity_days: int = Field(30, ge=1, le=730)


class RuleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    rule_type: str = "custom"
    segment_key: str
    segment_params: Dict[str, Any] = {}
    channel: str = "WHATSAPP"
    template_id: str
    trigger: TriggerSpec = TriggerSpec()
    scope: Literal["GLOBAL", "ENTITY", "STORE"] = "GLOBAL"
    entity_id: Optional[str] = None
    store_id: Optional[str] = None
    is_transactional: bool = False
    freq_cap_exempt: bool = False
    voucher_template: Optional[VoucherTemplate] = None
    active: bool = False  # safe default: seeded/created inert until toggled on.

    @field_validator("segment_key")
    @classmethod
    def _validate_segment(cls, v: str) -> str:
        if v not in seg.SEGMENT_KEYS:
            raise ValueError(f"segment_key must be one of {sorted(seg.SEGMENT_KEYS)}")
        return v

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, v: str) -> str:
        val = (v or "").upper().strip()
        if val not in VALID_CHANNELS:
            raise ValueError(f"channel must be one of {sorted(VALID_CHANNELS)}")
        return val

    @field_validator("rule_type")
    @classmethod
    def _validate_rule_type(cls, v: str) -> str:
        if v not in _RULE_TYPES:
            raise ValueError(f"rule_type must be one of {sorted(_RULE_TYPES)}")
        return v


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    rule_type: Optional[str] = None
    segment_key: Optional[str] = None
    segment_params: Optional[Dict[str, Any]] = None
    channel: Optional[str] = None
    template_id: Optional[str] = None
    trigger: Optional[TriggerSpec] = None
    scope: Optional[Literal["GLOBAL", "ENTITY", "STORE"]] = None
    entity_id: Optional[str] = None
    store_id: Optional[str] = None
    is_transactional: Optional[bool] = None
    freq_cap_exempt: Optional[bool] = None
    voucher_template: Optional[VoucherTemplate] = None
    active: Optional[bool] = None

    @field_validator("segment_key")
    @classmethod
    def _validate_segment(cls, v):
        if v is not None and v not in seg.SEGMENT_KEYS:
            raise ValueError(f"segment_key must be one of {sorted(seg.SEGMENT_KEYS)}")
        return v

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, v):
        if v is None:
            return v
        val = (v or "").upper().strip()
        if val not in VALID_CHANNELS:
            raise ValueError(f"channel must be one of {sorted(VALID_CHANNELS)}")
        return val

    @field_validator("rule_type")
    @classmethod
    def _validate_rule_type(cls, v):
        if v is not None and v not in _RULE_TYPES:
            raise ValueError(f"rule_type must be one of {sorted(_RULE_TYPES)}")
        return v


# ============================================================================
# HELPERS
# ============================================================================


def _now_iso() -> str:
    return datetime.now().isoformat()


def _new_rule_id() -> str:
    return f"RMD-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


def _strip(doc: Dict[str, Any]) -> Dict[str, Any]:
    if doc:
        doc.pop("_id", None)
    return doc


def _audit(
    db,
    rule_id: str,
    action: str,
    user: dict,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Append an immutable reminder_audit row (fail-soft). Mirrors the
    campaigns._audit shape; 'Audit Everything' (SYSTEM_INTENT)."""
    if db is None:
        return
    try:
        db.get_collection("reminder_audit").insert_one(
            {
                "audit_id": f"RMA-{uuid.uuid4().hex[:10].upper()}",
                "rule_id": rule_id,
                "action": action,
                "actor": user.get("user_id", "unknown"),
                "detail": detail or {},
                "at": _now_iso(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reminder audit write failed (%s): %s", action, exc)


def _get_rule_or_404(db, rule_id: str) -> Dict[str, Any]:
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db.get_collection("reminder_rules").find_one({"rule_id": rule_id})
    if not doc or doc.get("deleted_at"):
        raise HTTPException(status_code=404, detail="Reminder rule not found")
    return doc


def _require_admin_for_global(rule: Dict[str, Any], user: dict) -> None:
    """A GLOBAL or ENTITY rule may only be mutated by ADMIN+ (SUPERADMIN/ADMIN/
    AREA_MANAGER). A STORE-scope rule additionally goes through _enforce_store_scope
    so a STORE_MANAGER is locked to their own store."""
    scope = (rule.get("scope") or "GLOBAL").upper()
    roles = set(user.get("roles", []) or [])
    if scope in ("GLOBAL", "ENTITY"):
        if not (roles & {"SUPERADMIN", "ADMIN", "AREA_MANAGER"}):
            raise HTTPException(
                status_code=403,
                detail="GLOBAL/ENTITY reminder rules require ADMIN access",
            )
    else:
        _enforce_store_scope(rule, user)


def _e2_pick(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply E2 hierarchy (STORE > ENTITY > GLOBAL) per rule_type: when several
    rules share a rule_type, the most-specific scope wins. Returns the list with
    the winner first for each rule_type (callers that want a single resolved view
    read the first per type). Non-conflicting rules are returned unchanged."""
    rank = {"STORE": 3, "ENTITY": 2, "GLOBAL": 1}
    rules_sorted = sorted(
        rules,
        key=lambda r: rank.get((r.get("scope") or "GLOBAL").upper(), 0),
        reverse=True,
    )
    return rules_sorted


# ============================================================================
# ROUTES
# ============================================================================


@router.get("/rules")
async def list_rules(
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    active: Optional[bool] = Query(None),
    rule_type: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_READ_ROLES)),
):
    """List reminder rules, E2-scope-ordered (STORE > ENTITY > GLOBAL). A
    STORE-scope store_id filter returns that store's rules plus the GLOBAL/ENTITY
    rules that apply to it, with the store-scope winner first per rule_type."""
    db = _marketing_get_db()
    if db is None:
        return {"rules": [], "total": 0}
    # Exclude soft-deleted rules (deleted_at null OR field absent).
    query: Dict[str, Any] = {
        "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}]
    }
    if active is not None:
        query["active"] = active
    if rule_type:
        query["rule_type"] = rule_type
    # Store/entity filter: include the store's own rules + the broader scopes
    # that apply to it (so a store sees what actually governs it).
    if store_id:
        query["$and"] = [
            {
                "$or": [
                    {"store_id": store_id},
                    {"scope": "ENTITY", **({"entity_id": entity_id} if entity_id else {})},
                    {"scope": "GLOBAL"},
                ]
            }
        ]
    elif entity_id:
        query["$and"] = [
            {"$or": [{"entity_id": entity_id}, {"scope": "GLOBAL"}]}
        ]
    coll = db.get_collection("reminder_rules")
    rules = [_strip(r) for r in coll.find(query).limit(500)]
    rules = _e2_pick(rules)
    return {"rules": rules, "total": len(rules)}


@router.post("/rules")
async def create_rule(
    req: RuleCreate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Create a reminder rule. Defaults to active=False (inert until toggled)."""
    db = _marketing_get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc_scope = {"scope": req.scope, "store_id": req.store_id}
    _require_admin_for_global(doc_scope, current_user)
    if req.scope == "STORE" and not req.store_id:
        raise HTTPException(status_code=400, detail="STORE-scope rule requires store_id")
    if req.scope == "ENTITY" and not req.entity_id:
        raise HTTPException(status_code=400, detail="ENTITY-scope rule requires entity_id")

    rule_id = _new_rule_id()
    now = _now_iso()
    doc = {
        "rule_id": rule_id,
        "scope": req.scope,
        "entity_id": req.entity_id,
        "store_id": req.store_id,
        "name": req.name,
        "rule_type": req.rule_type,
        "segment_key": req.segment_key,
        "segment_params": req.segment_params or {},
        "channel": req.channel,
        "template_id": req.template_id,
        "trigger": req.trigger.model_dump(),
        "is_transactional": bool(req.is_transactional),
        "freq_cap_exempt": bool(req.freq_cap_exempt),
        "voucher_template": req.voucher_template.model_dump() if req.voucher_template else None,
        "active": bool(req.active),
        "last_run_at": None,
        "last_resolved": None,
        "sent_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "deleted_at": None,
        "created_by": current_user.get("user_id", "unknown"),
        "created_at": now,
        "updated_at": now,
    }
    db.get_collection("reminder_rules").insert_one(doc)
    _audit(db, rule_id, "CREATE", current_user, {"name": req.name, "active": req.active})
    return {"message": "Reminder rule created", "rule": _strip(dict(doc))}


@router.get("/rules/{rule_id}")
async def get_rule(
    rule_id: str,
    current_user: dict = Depends(require_roles(*_READ_ROLES)),
):
    """Fetch a single rule + a live audience estimate for its segment."""
    db = _marketing_get_db()
    doc = _get_rule_or_404(db, rule_id)
    if (doc.get("scope") or "GLOBAL").upper() == "STORE":
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


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    req: RuleUpdate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Edit a rule. Engine-owned counters are not editable."""
    db = _marketing_get_db()
    doc = _get_rule_or_404(db, rule_id)
    _require_admin_for_global(doc, current_user)

    updates = req.model_dump(exclude_unset=True)
    set_fields: Dict[str, Any] = {}
    for k, v in updates.items():
        if k in _PROTECTED_FIELDS:
            continue
        if k == "trigger" and v is not None:
            set_fields["trigger"] = v
        elif k == "voucher_template":
            set_fields["voucher_template"] = v  # may be None to clear
        else:
            set_fields[k] = v
    set_fields["updated_at"] = _now_iso()
    db.get_collection("reminder_rules").update_one(
        {"rule_id": rule_id}, {"$set": set_fields}
    )
    _audit(db, rule_id, "UPDATE", current_user, {"fields": sorted(set_fields.keys())})
    return _strip(_get_rule_or_404(db, rule_id))


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Soft-delete a rule (active=False + deleted_at). It stops running and
    drops out of the list, but the audit trail is preserved."""
    db = _marketing_get_db()
    doc = _get_rule_or_404(db, rule_id)
    _require_admin_for_global(doc, current_user)
    db.get_collection("reminder_rules").update_one(
        {"rule_id": rule_id},
        {"$set": {"active": False, "deleted_at": _now_iso(), "updated_at": _now_iso()}},
    )
    _audit(db, rule_id, "DELETE", current_user)
    return {"message": "Reminder rule deleted", "rule_id": rule_id}


@router.post("/rules/{rule_id}/toggle")
async def toggle_rule(
    rule_id: str,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Flip a rule's active flag. STORE-scope rules may be toggled by their
    STORE_MANAGER; GLOBAL/ENTITY rules require ADMIN+."""
    db = _marketing_get_db()
    doc = _get_rule_or_404(db, rule_id)
    _require_admin_for_global(doc, current_user)
    new_active = not bool(doc.get("active"))
    db.get_collection("reminder_rules").update_one(
        {"rule_id": rule_id},
        {"$set": {"active": new_active, "updated_at": _now_iso()}},
    )
    _audit(db, rule_id, "TOGGLE", current_user, {"active": new_active})
    return {"rule_id": rule_id, "active": new_active}


@router.post("/rules/{rule_id}/preview")
async def preview_rule(
    rule_id: str,
    current_user: dict = Depends(require_roles(*_READ_ROLES)),
):
    """Dry-run the rule: returns the audience size + per-gate suppression
    breakdown. Writes NOTHING (no notification, no ledger, no voucher, no task).
    """
    db = _marketing_get_db()
    doc = _get_rule_or_404(db, rule_id)
    if (doc.get("scope") or "GLOBAL").upper() == "STORE":
        _enforce_store_scope(doc, current_user)
    result = await reminder_rail.evaluate_rule(db, doc, dry_run=True)
    return result


@router.post("/rules/{rule_id}/run-now")
async def run_rule_now(
    rule_id: str,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Evaluate the rule immediately (queues PENDING messages -- DISPATCH_MODE
    gated; nothing goes live with the default off). Rate-limited per user."""
    db = _marketing_get_db()
    doc = _get_rule_or_404(db, rule_id)
    _require_admin_for_global(doc, current_user)
    rate_err = _check_notification_rate(current_user.get("user_id", "unknown"))
    if rate_err:
        raise HTTPException(status_code=429, detail=rate_err)
    result = await reminder_rail.evaluate_rule(db, doc, dry_run=False)
    db.get_collection("reminder_rules").update_one(
        {"rule_id": rule_id},
        {
            "$set": {
                "last_run_at": _now_iso(),
                "last_resolved": result.get("resolved", 0),
                "updated_at": _now_iso(),
            },
            "$inc": {
                "sent_count": result.get("queued", 0),
                "skipped_count": (
                    result.get("skipped_consent", 0)
                    + result.get("skipped_freqcap", 0)
                    + result.get("skipped_no_phone", 0)
                ),
                "failed_count": result.get("errors", 0),
            },
        },
    )
    _audit(db, rule_id, "RUN_NOW", current_user, result)
    return result


@router.get("/rules/{rule_id}/history")
async def rule_history(
    rule_id: str,
    limit: int = Query(100, le=500),
    current_user: dict = Depends(require_roles(*_READ_ROLES)),
):
    """notification_logs rows stamped with this rule_id, newest-first."""
    db = _marketing_get_db()
    doc = _get_rule_or_404(db, rule_id)
    if (doc.get("scope") or "GLOBAL").upper() == "STORE":
        _enforce_store_scope(doc, current_user)
    try:
        logs = list(
            db.get_collection("notification_logs")
            .find({"rule_id": rule_id})
            .sort("created_at", -1)
            .limit(limit)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reminder history read failed for %s: %s", rule_id, exc)
        logs = []
    out = []
    for log in logs:
        _strip(log)
        phone = log.get("customer_phone") or log.get("phone") or ""
        log["customer_phone_masked"] = (
            ("*" * max(0, len(phone) - 4) + phone[-4:]) if phone else ""
        )
        log.pop("customer_phone", None)
        out.append(log)
    return {"rule_id": rule_id, "history": out, "total": len(out)}
