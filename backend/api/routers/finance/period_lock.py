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

    # The payroll run and this lock share one guard document per month, so a
    # run that is being saved can never land behind the lock (see
    # payroll_period_guard). Read it BEFORE payroll: the commit below is
    # refused if any run touched the month after this read.
    from ...services import payroll_period_guard

    guard = payroll_period_guard.read(db, year, month)
    if guard and guard.get("locked"):
        # An earlier lock passed every check and committed, then stopped
        # before its period_locks row was written. Payroll has been refused
        # since, so nothing below can have changed: finish that lock.
        return _write_lock(db, month, year, guard)
    if payroll_period_guard.run_in_flight(guard):
        raise HTTPException(
            409,
            f"Payroll for {month}/{year} is being saved right now. Wait a "
            "minute, then lock the month again.",
        )

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
            "first, then lock the month. A locked month cannot be approved "
            "later, so its wages would never be counted.",
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
        "locked_by": current_user.get("user_id"),
        "locked_at": utc_now_iso(),
    }
    if not_run:
        lock["without_payroll_for"] = len(not_run)
    # The one atomic step: lock the guard only if no payroll run claimed,
    # wrote or finished since it was read above. From here every saving run is
    # refused, so the payroll that was just checked is the payroll that stays.
    if not payroll_period_guard.commit_lock(db, year, month, guard, lock):
        raise HTTPException(
            409,
            f"Payroll for {month}/{year} changed while the month was being "
            "locked. Check HR > Payroll Run, then lock the month again.",
        )
    return _write_lock(db, month, year, lock)


def _write_lock(db, month: int, year: int, decided: dict) -> dict:
    """Write the period_locks row every posting reads, from what the guard
    commit decided (who, when, and how many staff had no payroll)."""
    lock = {"month": month, "year": year}
    for field in ("locked_by", "locked_at", "without_payroll_for"):
        if decided.get(field) is not None:
            lock[field] = decided[field]
    db.get_collection("period_locks").insert_one(lock)
    return {"message": f"Period {month}/{year} locked", "month": month, "year": year}


@router.get("/period-locks")
async def get_period_locks(current_user: dict = Depends(get_current_user)):
    db = _get_db()
    if db is None:
        return []
    locks = list(db.get_collection("period_locks").find({}, {"_id": 0}))
    return locks
