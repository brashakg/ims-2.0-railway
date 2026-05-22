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

router = APIRouter()

_UNREAD_STATUSES = ["PENDING", "SENT", "DELIVERED"]


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
    limit: int = Query(30, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Current user's notifications, newest first, plus the unread count."""
    col = _coll()
    uid = current_user.get("user_id")
    if col is None or not uid:
        return {"notifications": [], "unread_count": 0, "total": 0}

    base = {"user_id": uid}
    query = dict(base)
    if unread_only:
        query["status"] = {"$in": _UNREAD_STATUSES}

    try:
        items = list(col.find(query, {"_id": 0}).sort("created_at", -1).limit(limit))
        unread = col.count_documents({**base, "status": {"$in": _UNREAD_STATUSES}})
        total = col.count_documents(base)
    except Exception:
        return {"notifications": [], "unread_count": 0, "total": 0}

    return {"notifications": items, "unread_count": unread, "total": total}


@router.get("/unread-count")
async def unread_count(current_user: dict = Depends(get_current_user)):
    """Lightweight badge poll."""
    col = _coll()
    uid = current_user.get("user_id")
    if col is None or not uid:
        return {"unread_count": 0}
    try:
        n = col.count_documents({"user_id": uid, "status": {"$in": _UNREAD_STATUSES}})
    except Exception:
        n = 0
    return {"unread_count": n}


@router.patch("/{notification_id}/read")
async def mark_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    """Mark one of the current user's notifications as read."""
    col = _coll()
    uid = current_user.get("user_id")
    if col is None or not uid:
        raise HTTPException(status_code=503, detail="DB unavailable")
    res = col.update_one(
        {"notification_id": notification_id, "user_id": uid},
        {"$set": {"status": "READ", "read_at": datetime.now()}},
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
        {"$set": {"status": "READ", "read_at": datetime.now()}},
    )
    return {"updated": res.modified_count}
