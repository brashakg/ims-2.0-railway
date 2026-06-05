"""
IMS 2.0 — Handoffs Router
==========================

A user-to-user file-handoff feature: any authenticated user uploads
an image / PDF (≤25 MB), assigns it to one or more recipients (Store
Manager / Accountant / Admin / SUPERADMIN), sets a 3-30 day TTL, and
the recipient sees it on their Hub. The recipient picks one of:

  approved · denied · accepted · received · reshared

plus an optional single-line comment.

Auto-deletion (TTL):
  - Mongo TTL index on `expires_at` removes the handoff doc.
  - The orphaned GridFS blob is cleaned up by NEXUS's hourly sweep.

Reshare (per user direction):
  - Creates a NEW handoff doc with `parent_handoff_id` set + the SAME
    `expires_at` as the original, so the chain inherits the original
    TTL window. Resharing does not extend the deadline.

Post-reply UX:
  - The recipient hits one of the 5 response options. The card on
    their Hub then opens a "dismiss / keep / snooze" modal — the
    server tracks `dismissed`, `kept`, and `snooze_until` per recipient.
  - The Hub query filters out cards where dismissed=True OR
    snooze_until > now (until snooze expires).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from .auth import get_current_user
from ..dependencies import get_handoff_repository, get_user_repository
from ..services.file_store import (
    get_file_store,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Roles eligible to be RECIPIENTS — per user direction. Senders may
# be anyone authenticated.
ELIGIBLE_RECIPIENT_ROLES = frozenset(
    {
        "STORE_MANAGER",
        "ACCOUNTANT",
        "ADMIN",
        "SUPERADMIN",
    }
)

# Allowed responses (must match frontend)
VALID_RESPONSES = frozenset(
    {
        "approved",
        "denied",
        "accepted",
        "received",
        "reshared",
    }
)

MIN_VALIDITY_DAYS = 3
MAX_VALIDITY_DAYS = 30


# ============================================================================
# Schemas
# ============================================================================


class HandoffResponseInput(BaseModel):
    response: str = Field(..., description="approved | denied | accepted | received")
    comment: Optional[str] = Field(None, max_length=200)

    @field_validator("response")
    @classmethod
    def _v_response(cls, v):
        v = (v or "").strip().lower()
        # Reshare goes through the dedicated /reshare endpoint
        allowed = VALID_RESPONSES - {"reshared"}
        if v not in allowed:
            raise ValueError(f"response must be one of {sorted(allowed)}")
        return v


class HandoffReshareInput(BaseModel):
    recipient_user_ids: List[str] = Field(..., min_length=1)
    comment: Optional[str] = Field(None, max_length=200)


class HandoffDismissInput(BaseModel):
    action: str = Field(..., description="dismiss | keep | snooze")
    snooze_minutes: Optional[int] = Field(
        None,
        ge=15,
        le=24 * 60 * 7,  # cap at 7 days
        description="Required if action=snooze. Minutes until card reappears.",
    )

    @field_validator("action")
    @classmethod
    def _v_action(cls, v):
        v = (v or "").strip().lower()
        if v not in {"dismiss", "keep", "snooze"}:
            raise ValueError("action must be dismiss | keep | snooze")
        return v


# ============================================================================
# Helpers
# ============================================================================


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scrub(doc: Optional[Dict]) -> Optional[Dict]:
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


def _user_view(handoff: Dict, user_id: str) -> Dict:
    """Trim a handoff doc to the per-user inbox view: the recipient
    sub-doc for THIS user, alongside the public top-level fields."""
    base = _scrub(handoff) or {}
    my_recipient = next(
        (r for r in base.get("recipients", []) if r.get("user_id") == user_id),
        None,
    )
    return {
        "handoff_id": base.get("handoff_id"),
        "uploader_id": base.get("uploader_id"),
        "uploader_name": base.get("uploader_name"),
        "title": base.get("title"),
        "description": base.get("description"),
        "file": base.get("file"),
        "created_at": base.get("created_at"),
        "expires_at": base.get("expires_at"),
        "validity_days": base.get("validity_days"),
        "parent_handoff_id": base.get("parent_handoff_id"),
        "my_status": (my_recipient or {}).get("status", "pending"),
        "my_response": (my_recipient or {}).get("response"),
        "my_comment": (my_recipient or {}).get("comment"),
        "my_responded_at": (my_recipient or {}).get("responded_at"),
        "my_dismissed": (my_recipient or {}).get("dismissed", False),
        "my_kept": (my_recipient or {}).get("kept", False),
        "my_snooze_until": (my_recipient or {}).get("snooze_until"),
    }


def _is_visible_on_hub(my_recipient: Dict, now: datetime) -> bool:
    """Visibility rule for the Hub inbox: hide if dismissed=True (and
    not kept), and hide if snoozed."""
    if my_recipient.get("kept"):
        return True  # explicit keep overrides everything
    if my_recipient.get("dismissed"):
        return False
    snooze = my_recipient.get("snooze_until")
    if snooze:
        try:
            snooze_dt = (
                snooze
                if isinstance(snooze, datetime)
                else datetime.fromisoformat(str(snooze).replace("Z", "+00:00"))
            )
            if snooze_dt > now:
                return False
        except Exception:
            pass
    return True


def _resolve_recipients(recipient_ids: List[str], user_repo) -> List[Dict]:
    """Look up each user_id; skip missing or role-ineligible. Returns a
    list of {user_id, user_name, role} stubs ready to embed in the
    handoff doc."""
    out: List[Dict] = []
    if user_repo is None:
        return out
    seen = set()
    for uid in recipient_ids:
        if not uid or uid in seen:
            continue
        seen.add(uid)
        u = user_repo.find_by_id(uid)
        if not u:
            continue
        roles = u.get("roles") or []
        if not any(r in ELIGIBLE_RECIPIENT_ROLES for r in roles):
            # Recipient must hold one of the eligible roles
            continue
        out.append(
            {
                "user_id": uid,
                "user_name": u.get("name")
                or u.get("full_name")
                or u.get("username")
                or uid,
                "role": next(
                    (r for r in roles if r in ELIGIBLE_RECIPIENT_ROLES),
                    roles[0] if roles else "",
                ),
                "status": "pending",
                "response": None,
                "comment": None,
                "responded_at": None,
                "dismissed": False,
                "kept": False,
                "snooze_until": None,
            }
        )
    return out


# ============================================================================
# Endpoints
# ============================================================================


@router.post("", status_code=201)
@router.post("/", status_code=201, include_in_schema=False)
async def create_handoff(
    file: UploadFile = File(...),
    title: str = Form(..., min_length=2, max_length=120),
    description: Optional[str] = Form(None),
    recipient_ids: str = Form(..., description="JSON array of user_ids"),
    validity_days: int = Form(7),
    current_user: dict = Depends(get_current_user),
):
    """Upload a file + create a handoff doc assigned to one or more
    eligible recipients."""
    # Validate inputs up front so the user gets clean 422s
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"File type '{mime}' not allowed. Accepted: {sorted(ALLOWED_MIME_TYPES)}",
        )
    if validity_days < MIN_VALIDITY_DAYS or validity_days > MAX_VALIDITY_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"validity_days must be between {MIN_VALIDITY_DAYS} and {MAX_VALIDITY_DAYS}",
        )
    try:
        recipient_id_list = json.loads(recipient_ids)
        if not isinstance(recipient_id_list, list) or not recipient_id_list:
            raise ValueError
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="recipient_ids must be a non-empty JSON array of user_id strings",
        )

    # Read the file payload + size-check before persisting anything
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB cap",
        )

    handoff_repo = get_handoff_repository()
    if handoff_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Resolve recipients via the user repository (validates each id +
    # skips role-ineligible users).
    user_repo = get_user_repository()
    recipients = _resolve_recipients(recipient_id_list, user_repo)
    if not recipients:
        raise HTTPException(
            status_code=400,
            detail="No eligible recipients (must hold STORE_MANAGER / ACCOUNTANT / ADMIN / SUPERADMIN)",
        )

    handoff_id = str(uuid.uuid4())

    # Persist file blob first; if that fails we don't write the doc.
    fs = get_file_store()
    if fs is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")
    file_id = fs.put(
        content=content,
        filename=file.filename,
        mime_type=mime,
        metadata={"handoff_id": handoff_id, "uploader_id": current_user.get("user_id")},
    )
    if not file_id:
        raise HTTPException(status_code=500, detail="File store write failed")

    now = _now()
    expires_at = now + timedelta(days=validity_days)
    doc = {
        "handoff_id": handoff_id,
        "uploader_id": current_user.get("user_id"),
        "uploader_name": current_user.get("name") or current_user.get("username", ""),
        "title": title.strip(),
        "description": (description or "").strip() or None,
        "file": {
            "file_id": file_id,
            "filename": file.filename,
            "mime_type": mime,
            "size_bytes": len(content),
        },
        "recipients": recipients,
        "created_at": now,
        "expires_at": expires_at,
        "validity_days": validity_days,
        "parent_handoff_id": None,
    }

    saved = handoff_repo.create(doc)
    if saved is None:
        # Best-effort cleanup of the orphaned blob
        try:
            fs.delete(file_id)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to persist handoff")

    return _scrub(saved)


@router.get("/inbox")
async def list_inbox(current_user: dict = Depends(get_current_user)):
    """Return handoffs visible on THIS user's Hub (pending or kept,
    not dismissed, not currently snoozed)."""
    repo = get_handoff_repository()
    if repo is None:
        return {"handoffs": [], "total": 0}
    uid = current_user.get("user_id")
    rows = repo.find_inbox_for_user(uid)
    now = _now()
    visible: List[Dict] = []
    for h in rows:
        my_r = next(
            (r for r in h.get("recipients", []) if r.get("user_id") == uid),
            None,
        )
        if my_r is None:
            continue
        if not _is_visible_on_hub(my_r, now):
            continue
        visible.append(_user_view(h, uid))
    return {"handoffs": visible, "total": len(visible)}


@router.get("/sent")
async def list_sent(current_user: dict = Depends(get_current_user)):
    """List handoffs uploaded by the current user."""
    repo = get_handoff_repository()
    if repo is None:
        return {"handoffs": [], "total": 0}
    rows = repo.find_sent_by_user(current_user.get("user_id"))
    return {"handoffs": [_scrub(r) for r in rows], "total": len(rows)}


@router.get("/{handoff_id}")
async def get_handoff(
    handoff_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Fetch a single handoff doc. Permission: uploader, any recipient,
    or SUPERADMIN."""
    repo = get_handoff_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    handoff = repo.find_by_id(handoff_id)
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")

    uid = current_user.get("user_id")
    roles = current_user.get("roles") or []
    is_uploader = handoff.get("uploader_id") == uid
    is_recipient = any(r.get("user_id") == uid for r in handoff.get("recipients", []))
    is_super = "SUPERADMIN" in roles
    if not (is_uploader or is_recipient or is_super):
        raise HTTPException(
            status_code=403, detail="Not authorised to view this handoff"
        )

    return _scrub(handoff)


@router.get("/{handoff_id}/file")
async def download_handoff_file(
    handoff_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Stream the underlying file. Same permission gate as GET."""
    repo = get_handoff_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    handoff = repo.find_by_id(handoff_id)
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")

    uid = current_user.get("user_id")
    roles = current_user.get("roles") or []
    is_uploader = handoff.get("uploader_id") == uid
    is_recipient = any(r.get("user_id") == uid for r in handoff.get("recipients", []))
    is_super = "SUPERADMIN" in roles
    if not (is_uploader or is_recipient or is_super):
        raise HTTPException(status_code=403, detail="Not authorised to view this file")

    file_meta = handoff.get("file") or {}
    file_id = file_meta.get("file_id")
    if not file_id:
        raise HTTPException(status_code=404, detail="File reference missing")

    fs = get_file_store()
    if fs is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")
    blob = fs.get(file_id)
    if blob is None:
        raise HTTPException(status_code=404, detail="File no longer available")

    content, filename, mime_type = blob
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


@router.post("/{handoff_id}/respond")
async def respond_handoff(
    handoff_id: str,
    payload: HandoffResponseInput,
    current_user: dict = Depends(get_current_user),
):
    """Recipient submits one of the canonical responses + optional
    single-line comment. Once submitted the response is final."""
    repo = get_handoff_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    handoff = repo.find_by_id(handoff_id)
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")

    uid = current_user.get("user_id")
    my_r = next(
        (r for r in handoff.get("recipients", []) if r.get("user_id") == uid), None
    )
    if my_r is None:
        raise HTTPException(
            status_code=403, detail="You are not a recipient of this handoff"
        )
    if my_r.get("status") == "responded":
        raise HTTPException(
            status_code=409,
            detail=f"Already responded ({my_r.get('response')}); cannot change",
        )

    updates = {
        "status": "responded",
        "response": payload.response,
        "comment": (payload.comment or "").strip() or None,
        "responded_at": _now(),
    }
    ok = repo.update_recipient(handoff_id, uid, updates)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to record response")

    fresh = repo.find_by_id(handoff_id)
    return _user_view(fresh or handoff, uid)


@router.post("/{handoff_id}/reshare", status_code=201)
async def reshare_handoff(
    handoff_id: str,
    payload: HandoffReshareInput,
    current_user: dict = Depends(get_current_user),
):
    """Forward a handoff to new recipients. Creates a NEW handoff doc
    pointing at the SAME GridFS file_id (no duplication of bytes), with
    `parent_handoff_id` linking back to the original. The original
    recipient's status is marked `responded` with response='reshared'.

    Per user direction: TTL stays anchored to the ORIGINAL upload
    date — `expires_at` is copied verbatim from the parent. A reshare
    can never extend the deadline.
    """
    repo = get_handoff_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    parent = repo.find_by_id(handoff_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="Handoff not found")

    uid = current_user.get("user_id")
    my_r = next(
        (r for r in parent.get("recipients", []) if r.get("user_id") == uid), None
    )
    if my_r is None:
        raise HTTPException(
            status_code=403, detail="Only a recipient can reshare a handoff"
        )

    # Resolve the new recipients (must hold an eligible role)
    user_repo = get_user_repository()
    new_recipients = _resolve_recipients(payload.recipient_user_ids, user_repo)
    if not new_recipients:
        raise HTTPException(status_code=400, detail="No eligible recipients in reshare")

    new_handoff_id = str(uuid.uuid4())
    parent_file = parent.get("file") or {}
    parent_expires = parent.get("expires_at") or _now() + timedelta(
        days=MIN_VALIDITY_DAYS
    )

    new_doc = {
        "handoff_id": new_handoff_id,
        "uploader_id": uid,
        "uploader_name": current_user.get("name") or current_user.get("username", ""),
        "title": parent.get("title"),
        "description": ((payload.comment or "").strip() or parent.get("description")),
        "file": parent_file,  # same GridFS reference — no duplication
        "recipients": new_recipients,
        "created_at": _now(),
        "expires_at": parent_expires,
        "validity_days": parent.get("validity_days"),
        "parent_handoff_id": parent.get("handoff_id"),
    }

    saved = repo.create(new_doc)
    if saved is None:
        raise HTTPException(
            status_code=500, detail="Failed to persist reshared handoff"
        )

    # Mark the parent recipient's record as 'reshared'
    repo.update_recipient(
        handoff_id,
        uid,
        {
            "status": "responded",
            "response": "reshared",
            "comment": (payload.comment or "").strip() or None,
            "responded_at": _now(),
        },
    )

    return _scrub(saved)


@router.post("/{handoff_id}/dismiss")
async def dismiss_handoff(
    handoff_id: str,
    payload: HandoffDismissInput,
    current_user: dict = Depends(get_current_user),
):
    """Recipient's per-card visibility action: dismiss / keep / snooze.
    Mutates only the calling recipient's sub-doc."""
    repo = get_handoff_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    handoff = repo.find_by_id(handoff_id)
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")

    uid = current_user.get("user_id")
    my_r = next(
        (r for r in handoff.get("recipients", []) if r.get("user_id") == uid), None
    )
    if my_r is None:
        raise HTTPException(
            status_code=403, detail="You are not a recipient of this handoff"
        )

    if payload.action == "dismiss":
        updates = {"dismissed": True, "kept": False, "snooze_until": None}
    elif payload.action == "keep":
        updates = {"kept": True, "dismissed": False, "snooze_until": None}
    else:  # snooze
        if not payload.snooze_minutes:
            raise HTTPException(
                status_code=400, detail="snooze_minutes required for action=snooze"
            )
        snooze_until = _now() + timedelta(minutes=payload.snooze_minutes)
        updates = {"snooze_until": snooze_until, "dismissed": False, "kept": False}

    ok = repo.update_recipient(handoff_id, uid, updates)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update handoff")
    fresh = repo.find_by_id(handoff_id)
    return _user_view(fresh or handoff, uid)


@router.delete("/{handoff_id}")
async def revoke_handoff(
    handoff_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Uploader can revoke a handoff before TTL. Best-effort GridFS
    cleanup happens here too (orphan sweep is the fallback)."""
    repo = get_handoff_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")
    handoff = repo.find_by_id(handoff_id)
    if handoff is None:
        raise HTTPException(status_code=404, detail="Handoff not found")

    uid = current_user.get("user_id")
    roles = current_user.get("roles") or []
    is_uploader = handoff.get("uploader_id") == uid
    is_super = "SUPERADMIN" in roles
    if not (is_uploader or is_super):
        raise HTTPException(
            status_code=403, detail="Only the uploader can revoke a handoff"
        )

    file_id = (handoff.get("file") or {}).get("file_id")
    repo.delete(handoff_id)
    if file_id:
        fs = get_file_store()
        if fs is not None:
            try:
                fs.delete(file_id)
            except Exception:
                pass
    return {"deleted": True, "handoff_id": handoff_id}


@router.get("/eligible-recipients/list")
async def list_eligible_recipients(
    q: Optional[str] = Query(None, description="Search by name or username"),
    current_user: dict = Depends(get_current_user),
):
    """Return the universe of users this caller can SEND to: anyone
    holding STORE_MANAGER / ACCOUNTANT / ADMIN / SUPERADMIN. Optional
    name/username substring filter."""
    user_repo = get_user_repository()
    if user_repo is None:
        return {"recipients": [], "total": 0}
    needle = (q or "").strip().lower()
    out: List[Dict[str, Any]] = []
    try:
        # Pull all users; filter in-Python (eligible-role population is small).
        users = user_repo.find_many({}, limit=500) or []
    except Exception:
        users = []
    for u in users:
        roles = u.get("roles") or []
        if not any(r in ELIGIBLE_RECIPIENT_ROLES for r in roles):
            continue
        # Don't suggest yourself
        if u.get("user_id") == current_user.get("user_id"):
            continue
        name = (u.get("name") or u.get("full_name") or "").strip()
        username = (u.get("username") or "").strip()
        if needle and needle not in name.lower() and needle not in username.lower():
            continue
        out.append(
            {
                "user_id": u.get("user_id"),
                "name": name or username,
                "username": username,
                "role": next(
                    (r for r in roles if r in ELIGIBLE_RECIPIENT_ROLES), roles[0]
                ),
            }
        )
    out.sort(key=lambda r: (r["role"], r["name"]))
    return {"recipients": out, "total": len(out)}
