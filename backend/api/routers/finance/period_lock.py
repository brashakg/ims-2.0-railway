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
    without_payroll: bool = False,
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

    # A locked month refuses payroll approval (423) and nothing unlocks a
    # month, so a payroll run still in DRAFT would be stuck there for good and
    # its wages would never reach the P&L, Tally or the PF file (they count
    # APPROVED and PAID rows only). The lock is chain-wide, so any shop's
    # draft stops it.
    payroll_rows = list(
        db.get_collection("payroll").find({"month": month, "year": year})
    )
    drafts = [r for r in payroll_rows if r.get("status") == "DRAFT"]
    if drafts:
        shops = ", ".join(sorted({str(d.get("store_id") or "no shop") for d in drafts}))
        raise HTTPException(
            409,
            f"Payroll for {month}/{year} is still a draft for {len(drafts)} "
            f"employee(s) (shop: {shops}). Approve it in HR > Payroll Run "
            "first (or discard the draft there if it is wrong), then lock the "
            "month. A locked month cannot be approved later, so its wages "
            "would never be counted.",
        )

    # The same loss by the likelier order of events: the books are closed in
    # the first days of the next month and payroll is run a few days later. A
    # saving run answers 423 in a locked month, so those wages could never be
    # recorded. Unlike a draft this can be meant (a month from before payroll
    # was kept here), so the admin may say "lock anyway" (without_payroll).
    from ..payroll import _scope_configs

    has_row = {r.get("employee_id") for r in payroll_rows}
    not_run = [
        c for c in _scope_configs(db, None, None) if c.get("employee_id") not in has_row
    ]
    if not_run and not without_payroll:
        shops = ", ".join(sorted({str(c.get("store_id") or "no shop") for c in not_run}))
        raise HTTPException(
            409,
            {
                "code": "PAYROLL_NOT_RUN",
                "message": f"Payroll for {month}/{year} has not been run for "
                f"{len(not_run)} employee(s) (shop: {shops}). Once the month is "
                "locked it can never be run, so their wages for this month "
                "would never be counted. Run and approve it in HR > Payroll "
                "Run first.",
            },
        )

    lock = {
        "month": month,
        "year": year,
        "locked_by": current_user.get("user_id"),
        "locked_at": utc_now_iso(),
    }
    if not_run:
        lock["without_payroll_for"] = len(not_run)
    db.get_collection("period_locks").insert_one(lock)
    return {"message": f"Period {month}/{year} locked", "month": month, "year": year}


@router.get("/period-locks")
async def get_period_locks(current_user: dict = Depends(get_current_user)):
    db = _get_db()
    if db is None:
        return []
    locks = list(db.get_collection("period_locks").find({}, {"_id": 0}))
    return locks
