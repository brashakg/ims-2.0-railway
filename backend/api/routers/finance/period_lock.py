"""Accounting period lock (`/period-lock`, `/period-locks`).

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from api.utils.event_time import utc_now_iso

from datetime import datetime
from fastapi import Depends, HTTPException
from ..auth import get_current_user
from ._shared import _get_db, router

# === Period Lock ===


@router.post("/period-lock")
async def lock_period(
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    if "SUPERADMIN" not in current_user.get(
        "roles", []
    ) and "ADMIN" not in current_user.get("roles", []):
        raise HTTPException(403, "Only admin/superadmin can lock periods")

    if db is None:
        raise HTTPException(503, "Database not available")

    # Only a real month that has started can close: a mistyped lock (month
    # 13, or 05/2062) would block every payment put on a bill, and nothing
    # reopens it. The month is the IST one, as every other money date.
    from ...utils.ist import now_ist_naive

    now = now_ist_naive()
    if not 1 <= month <= 12 or not 2017 <= year or (year, month) > (now.year, now.month):
        raise HTTPException(
            422, f"{month}/{year} is not a month that has started - check the month and year"
        )

    existing = db.get_collection("period_locks").find_one(
        {"month": month, "year": year}
    )
    if existing:
        raise HTTPException(400, f"Period {month}/{year} is already locked")

    db.get_collection("period_locks").insert_one(
        {
            "month": month,
            "year": year,
            "locked_by": current_user.get("user_id"),
            "locked_at": utc_now_iso(),
        }
    )
    return {"message": f"Period {month}/{year} locked", "month": month, "year": year}


@router.get("/period-locks")
async def get_period_locks(current_user: dict = Depends(get_current_user)):
    db = _get_db()
    if db is None:
        return []
    locks = list(db.get_collection("period_locks").find({}, {"_id": 0}))
    return locks
