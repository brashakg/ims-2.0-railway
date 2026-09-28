"""Workshop root + the board: pending / overdue / ready lists, technician
workload and the dashboard KPIs.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import Depends, Query
from typing import Optional
from datetime import date, datetime
from ..auth import get_current_user
from ...dependencies import get_db, get_workshop_repository, validate_store_access
from ._shared import logger, router
from .helpers import _stamp_job_actor_names, job_to_frontend


# ============================================================================
# ENDPOINTS
# ============================================================================


# NOTE: Specific routes MUST come before /jobs/{job_id}


@router.get("")
@router.get("/")
async def get_workshop_root():
    """Root endpoint for workshop job list"""
    return {
        "module": "workshop",
        "status": "active",
        "message": "workshop jobs endpoint ready",
    }


@router.get("/pending")
async def get_pending_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get pending workshop jobs"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_pending(active_store)
        _stamp_job_actor_names(jobs)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/overdue")
async def get_overdue_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get overdue workshop jobs"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_overdue(active_store)
        _stamp_job_actor_names(jobs)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/ready")
async def get_ready_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get jobs ready for delivery"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_ready(active_store)
        _stamp_job_actor_names(jobs)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/technician-workload")
async def get_technician_workload(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get technician workload summary"""
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo and active_store:
        workload = repo.get_technician_workload(active_store)
        return {"workload": workload}

    return {"workload": []}


# ---------------------------------------------------------------------------
# Phase 6.4 — single-shot KPIs for the workshop dashboard header.
# The frontend currently computes Active / Urgent / Ready / Overdue on the
# client from the full job list. That works at small scale but means every
# workshop page load pulls every job in the store. This endpoint lets the
# client drop 4 HTTP calls and ~a few hundred KB of JSON in favour of one
# small summary call — and as a bonus exposes `avg_turnaround_days` and
# `completed_today` which the client couldn't cheaply compute before.
# ---------------------------------------------------------------------------


@router.get("/dashboard-kpis")
async def get_dashboard_kpis(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Aggregated workshop KPIs for the dashboard header.

    Returns:
        pending            — PENDING + IN_PROGRESS (i.e. "Active Jobs")
        in_progress        — IN_PROGRESS only
        qc_failed          — jobs sent back for rework
        ready_for_pickup   — READY status
        overdue            — pending/in_progress past expected_date
        completed_today    — COMPLETED or READY with completed_at == today
        delivered_today    — DELIVERED with status_updated_at == today
        avg_turnaround_days — mean (completed_at - created_at) across the
                              last 100 closed jobs. `None` if fewer than 5
                              samples exist (avoids noisy averages).

    Fail-soft: repo absent → returns zeros with null turnaround, never raises.
    """
    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    empty = {
        "pending": 0,
        "in_progress": 0,
        "qc_failed": 0,
        "ready_for_pickup": 0,
        "overdue": 0,
        "completed_today": 0,
        "delivered_today": 0,
        "avg_turnaround_days": None,
        "per_station_counts": {},
        "avg_dwell_by_station": {},
        "spoilage_cost_mtd_paise": 0,
        "remake_rate_pct": 0.0,
        "store_id": active_store,
        "as_of": datetime.now().isoformat(),
    }

    if repo is None or not active_store:
        return empty

    try:
        # One pass over the store's jobs so we don't hit Mongo five times
        # for what is effectively a group-by-status.
        all_jobs = repo.find_by_store(active_store)
    except Exception:
        return empty

    now = datetime.now()
    today_str = now.date().isoformat()

    pending = 0
    in_progress = 0
    qc_failed = 0
    ready = 0
    overdue = 0
    completed_today = 0
    delivered_today = 0
    turnaround_samples = []
    # F13 -- remake/spoilage rollup (same single walk; additive keys).
    month_str = today_str[:7]  # "YYYY-MM"
    jobs_with_remake = 0
    spoilage_cost_mtd_paise = 0

    for job in all_jobs:
        status = job.get("status", "")

        # F13: month-to-date spoilage cost + remake incidence.
        remakes = [e for e in (job.get("remake_reasons") or []) if isinstance(e, dict)]
        if remakes:
            jobs_with_remake += 1
            for entry in remakes:
                at = entry.get("at")
                at_s = at.isoformat() if isinstance(at, datetime) else str(at or "")
                if at_s.startswith(month_str):
                    try:
                        spoilage_cost_mtd_paise += max(
                            0, int(entry.get("cost_paise") or 0)
                        )
                    except (TypeError, ValueError):
                        pass

        if status == "PENDING":
            pending += 1
        elif status == "IN_PROGRESS":
            in_progress += 1
            pending += 1  # "Active" is PENDING + IN_PROGRESS in the UI
        elif status == "QC_FAILED":
            qc_failed += 1
        elif status == "READY":
            ready += 1

        # Overdue = open-ish job whose expected_date is BEFORE today.
        # Bug fixed: the previous code parsed the stored date-only string
        # ("2026-05-30") into a full datetime (midnight UTC), then compared
        # it against datetime.now() which includes the current time. Jobs due
        # TODAY would appear as overdue if any time had elapsed that day
        # because "2026-05-30T00:00:00" < "2026-05-30T14:00:00".
        # We now compare date-only strings so a job is only overdue when its
        # expected_date is STRICTLY BEFORE today.
        if status in ("PENDING", "IN_PROGRESS"):
            expected = job.get("expected_date")
            if expected:
                try:
                    if isinstance(expected, str):
                        # Take just the date portion (first 10 chars)
                        exp_date_str = expected[:10]
                    elif isinstance(expected, datetime):
                        exp_date_str = expected.date().isoformat()
                    elif isinstance(expected, date):
                        exp_date_str = expected.isoformat()
                    else:
                        exp_date_str = None
                    if exp_date_str is not None and exp_date_str < today_str:
                        overdue += 1
                except (ValueError, TypeError):
                    pass

        # Today counts — both by completed_at and by status_updated_at for
        # DELIVERED, so the "what shipped today" number is always live.
        completed_at = job.get("completed_at")
        if completed_at:
            ca_str = (
                completed_at
                if isinstance(completed_at, str)
                else completed_at.isoformat()
            )
            if ca_str.startswith(today_str):
                completed_today += 1

        if status == "DELIVERED":
            sua = job.get("status_updated_at")
            if sua:
                sua_str = sua if isinstance(sua, str) else sua.isoformat()
                if sua_str.startswith(today_str):
                    delivered_today += 1

        # Turnaround sample — only include finished jobs with both ts.
        if status in ("COMPLETED", "READY", "DELIVERED"):
            ca = job.get("completed_at")
            cr = job.get("created_at")
            if ca and cr:
                try:
                    ca_dt = (
                        ca
                        if isinstance(ca, datetime)
                        else datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
                    )
                    cr_dt = (
                        cr
                        if isinstance(cr, datetime)
                        else datetime.fromisoformat(str(cr).replace("Z", "+00:00"))
                    )
                    days = (ca_dt - cr_dt).total_seconds() / 86400.0
                    if days >= 0:
                        turnaround_samples.append(days)
                except (ValueError, TypeError):
                    pass

    # Cap sample size — latest 100 closed jobs are plenty and we've already
    # walked them; take the tail for a rolling view rather than all-time.
    if len(turnaround_samples) >= 5:
        recent = turnaround_samples[-100:]
        avg_turnaround = round(sum(recent) / len(recent), 2)
    else:
        avg_turnaround = None

    # F2 -- per-station live counts + avg dwell. ADDITIVE: existing keys above
    # are unchanged; these two are appended. Reuses the same all_jobs walk.
    per_station_counts: dict = {}
    avg_dwell_by_station: dict = {}
    try:
        from ...services import lab_routing

        stations = lab_routing.list_stations(get_db(), active_store)
        station_kpis = lab_routing.station_kpis(all_jobs, stations)
        per_station_counts = station_kpis.get("per_station_counts", {})
        avg_dwell_by_station = station_kpis.get("avg_dwell_by_station", {})
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] station KPIs failed: %s", e)

    # F13 -- remake rate over the same job population the other KPIs use.
    remake_rate_pct = (
        round(100.0 * jobs_with_remake / len(all_jobs), 1) if all_jobs else 0.0
    )

    return {
        "pending": pending,  # PENDING + IN_PROGRESS
        "in_progress": in_progress,
        "qc_failed": qc_failed,
        "ready_for_pickup": ready,
        "overdue": overdue,
        "completed_today": completed_today,
        "delivered_today": delivered_today,
        "avg_turnaround_days": avg_turnaround,
        "per_station_counts": per_station_counts,
        "avg_dwell_by_station": avg_dwell_by_station,
        "spoilage_cost_mtd_paise": spoilage_cost_mtd_paise,
        "remake_rate_pct": remake_rate_pct,
        "store_id": active_store,
        "as_of": now.isoformat(),
    }
