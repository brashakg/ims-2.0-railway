"""
IMS 2.0 - In-app notifications (staff bell)
===========================================
User-targeted notifications shown in the topbar bell. Backed by the
`notifications` collection (NOTIFICATION_SCHEMA). Written by the task
escalation engine (services.task_notify) and readable here per-user.

Distinct from marketing /notifications/* (customer-facing notification_logs).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from datetime import datetime

from .auth import get_current_user
from ..dependencies import get_db
from ..utils.event_time import parse_event_utc_naive, utc_now


def _utc_now() -> datetime:
    """One clock for this collection (audit R06): stored instants are NAIVE
    UTC -- what Mongo hands back and what the screens read a zone-less value
    as. The host's local clock (IST on a shop PC) made a 1-hour snooze look
    like the past and could not be compared with stored values."""
    return utc_now().replace(tzinfo=None)

router = APIRouter()

_UNREAD_STATUSES = ["PENDING", "SENT", "DELIVERED"]
MAX_SNOOZE = 3  # a notification may be snoozed at most 3 times


def _not_snoozed(now: datetime) -> dict:
    """Match notifications that are NOT currently snoozed (no snooze, or the
    snooze window has elapsed) so they re-surface in the bell/list."""
    return {
        "$or": [
            {"snoozed_until": {"$exists": False}},
            {"snoozed_until": None},
            {"snoozed_until": {"$lte": now}},
        ]
    }


def _coll():
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("notifications")
    except Exception:
        return None


@router.get("")
@router.get("/")
async def list_notifications(
    unread_only: bool = Query(False),
    include_snoozed: bool = Query(
        False, description="Include currently-snoozed notifications (full screen)"
    ),
    limit: int = Query(30, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Current user's notifications, newest first, plus the unread count.

    Currently-snoozed notifications are hidden by default (bell/dashboard) and
    only included when include_snoozed=True (the full notifications screen).
    The unread_count always excludes currently-snoozed items.
    """
    col = _coll()
    uid = current_user.get("user_id")
    if not uid:
        return {"notifications": [], "unread_count": 0, "total": 0}
    if col is None:
        # Audit R40: an unreadable list is not an empty one.
        raise HTTPException(status_code=503, detail="Notifications are unavailable right now")

    now = _utc_now()
    base = {"user_id": uid}
    query = dict(base)
    if unread_only:
        query["status"] = {"$in": _UNREAD_STATUSES}
    if not include_snoozed:
        query.update(_not_snoozed(now))

    try:
        items = list(col.find(query, {"_id": 0}).sort("created_at", -1).limit(limit))
        unread = col.count_documents(
            {**base, "status": {"$in": _UNREAD_STATUSES}, **_not_snoozed(now)}
        )
        total = col.count_documents(base)
    except Exception:
        raise HTTPException(status_code=503, detail="Notifications are unavailable right now")

    return {"notifications": items, "unread_count": unread, "total": total}


@router.get("/unread-count")
async def unread_count(current_user: dict = Depends(get_current_user)):
    """Lightweight badge poll."""
    col = _coll()
    uid = current_user.get("user_id")
    if not uid:
        return {"unread_count": 0}
    if col is None:
        raise HTTPException(status_code=503, detail="Notifications are unavailable right now")
    try:
        n = col.count_documents(
            {
                "user_id": uid,
                "status": {"$in": _UNREAD_STATUSES},
                **_not_snoozed(_utc_now()),
            }
        )
    except Exception:
        # Audit R40: a failed count is unknown, never a reassuring 0.
        raise HTTPException(status_code=503, detail="Notifications are unavailable right now")
    return {"unread_count": n}


@router.patch("/{notification_id}/read")
async def mark_read(
    notification_id: str, current_user: dict = Depends(get_current_user)
):
    """Mark one of the current user's notifications as read."""
    col = _coll()
    uid = current_user.get("user_id")
    if col is None or not uid:
        raise HTTPException(status_code=503, detail="DB unavailable")
    res = col.update_one(
        {"notification_id": notification_id, "user_id": uid},
        {"$set": {"status": "READ", "read_at": _utc_now()}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"notification_id": notification_id, "status": "READ"}


@router.post("/mark-all-read")
async def mark_all_read(current_user: dict = Depends(get_current_user)):
    """Mark every unread notification for the current user as read."""
    col = _coll()
    uid = current_user.get("user_id")
    if col is None or not uid:
        return {"updated": 0}
    res = col.update_many(
        {"user_id": uid, "status": {"$in": _UNREAD_STATUSES}},
        {"$set": {"status": "READ", "read_at": _utc_now()}},
    )
    return {"updated": res.modified_count}


@router.post("/{notification_id}/snooze")
async def snooze_notification(
    notification_id: str,
    until: str = Query(..., description="ISO datetime to snooze until"),
    current_user: dict = Depends(get_current_user),
):
    """Snooze a notification until a custom time. Max 3 snoozes per item."""
    col = _coll()
    uid = current_user.get("user_id")
    if col is None or not uid:
        raise HTTPException(status_code=503, detail="DB unavailable")

    # Audit R06: an offset (the screens send ...Z) is converted to UTC, not
    # dropped; a zone-less value is read as UTC, the frame stored here.
    until_dt = parse_event_utc_naive(until)
    if until_dt is None:
        raise HTTPException(status_code=400, detail="Invalid 'until' datetime")

    if until_dt <= _utc_now():
        raise HTTPException(status_code=400, detail="Snooze time must be in the future")

    doc = col.find_one({"notification_id": notification_id, "user_id": uid})
    if doc is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    count = int(doc.get("snooze_count", 0) or 0)
    if count >= MAX_SNOOZE:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_SNOOZE} snoozes reached for this notification",
        )

    col.update_one(
        {"notification_id": notification_id, "user_id": uid},
        {
            "$set": {"snoozed_until": until_dt, "snoozed_at": _utc_now()},
            "$inc": {"snooze_count": 1},
        },
    )
    return {
        "notification_id": notification_id,
        "snoozed_until": until_dt.isoformat() + "Z",
        "snooze_count": count + 1,
        "snoozes_remaining": MAX_SNOOZE - (count + 1),
    }
