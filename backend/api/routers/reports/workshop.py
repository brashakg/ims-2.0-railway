"""Staff ranking, workshop pending / productivity and daily stock count."""

from fastapi import Depends, Query
from typing import Any, Dict, Optional
from datetime import date, datetime, timedelta
from ...utils.ist import (
    now_ist_naive,
    ist_day_start_utc,
    ist_today,
)
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_order_repository,
    get_stock_repository,
    get_db,
    validate_store_access,
)
from ...services.name_resolver import order_actor_id, order_actor_name_map
from ._shared import (
    _REPORT_FINANCE_ROLES,
    _order_revenue,
    _orders_in_window,
    _row_category,
    _stock_category_map,
    router,
)

# ============================================================================
# STAFF & CLINICAL REPORTS
# ============================================================================


@router.get("/staff/ranking")
async def staff_ranking(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Staff performance ranking (sales, orders, avg bill); store-scoped."""
    active_store = validate_store_access(store_id, current_user)
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": []}

    # BUG-104, BOUND rule: found in the round-3 closing sweep. This is the
    # SECOND staff roster (besides /sales/by-salesperson): the two must rank
    # the same orders for the same typed range or a manager sees two
    # different rosters for one month.
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )

    # Same credit rule + name lookup as /sales/by-salesperson above (shared, not
    # copied -- two rosters that disagree on who sold is the defect this pair
    # already shipped once).
    names = order_actor_name_map(get_db(), orders)
    staff_data = {}
    for order in orders:
        staff_id = order_actor_id(order)
        staff_name = names.get(staff_id) or staff_id
        if staff_id not in staff_data:
            staff_data[staff_id] = {
                "staff_id": staff_id,
                "staff_name": staff_name,
                "total_sales": 0,
                "order_count": 0,
                "avg_bill": 0,
            }
        staff_data[staff_id]["total_sales"] += _order_revenue(order)
        staff_data[staff_id]["order_count"] += 1

    for staff in staff_data.values():
        if staff["order_count"] > 0:
            staff["avg_bill"] = round(staff["total_sales"] / staff["order_count"], 2)

    # Sort by total sales descending
    ranked = sorted(staff_data.values(), key=lambda x: x["total_sales"], reverse=True)

    return {"data": ranked}


# ============================================================================
# WORKSHOP & STOCK REPORTS
# ============================================================================


@router.get("/workshop/pending-jobs")
async def pending_workshop_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Pending workshop jobs report — Phase 6.4.

    Rewritten to query the `workshop_jobs` collection via WorkshopJobRepository
    (the previous implementation used the generic `tasks` collection with
    task_type='workshop_job', which the real workshop flow never populates).

    Response shape:
        {
            "data": [  # one row per pending job, sorted by age desc
                { "job_id", "job_number", "order_id", "status",
                  "technician_id", "expected_date", "created_at",
                  "age_days", "aging_bucket" } ],
            "summary": {
                "total_pending": int,
                "overdue": int,
                "by_aging_bucket": {"0-3d": n, "3-7d": n, "7+d": n},
                "by_technician": [ {"technician_id", "count"} ],
            },
        }

    Aging buckets are computed from created_at. `age_days` is the number of
    days the job has been sitting in PENDING or IN_PROGRESS. A job whose
    `expected_date` has passed is counted as overdue regardless of age.
    """
    from database.repositories.workshop_repository import WorkshopJobRepository  # lazy
    from ...dependencies import get_db as _get_db

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    db = _get_db()
    if db is None or not getattr(db, "is_connected", True):
        return {
            "data": [],
            "summary": {
                "total_pending": 0,
                "overdue": 0,
                "by_aging_bucket": {"0-3d": 0, "3-7d": 0, "7+d": 0},
                "by_technician": [],
            },
        }

    repo = WorkshopJobRepository(db.get_collection("workshop_jobs"))
    jobs = repo.find_pending(
        active_store
    )  # PENDING + IN_PROGRESS, sorted by expected_date

    now = now_ist_naive()
    data = []
    bucket_counts = {"0-3d": 0, "3-7d": 0, "7+d": 0}
    tech_counts = {}
    overdue_count = 0

    for job in jobs:
        created = job.get("created_at")
        expected = job.get("expected_date")

        # Age in days from created_at. Defensive parse for str or datetime.
        age_days = None
        if created:
            try:
                cr_dt = (
                    created
                    if isinstance(created, datetime)
                    else datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                )
                # Normalize tz — the stored timestamps are naive in dev but
                # may be tz-aware in prod Mongo. Strip tz for the subtraction.
                if cr_dt.tzinfo is not None:
                    cr_dt = cr_dt.replace(tzinfo=None)
                age_days = max(0, (now - cr_dt).days)
            except (ValueError, TypeError):
                age_days = None

        if age_days is None:
            bucket = "0-3d"  # unknown age — treat as fresh so we don't panic-escalate
        elif age_days < 3:
            bucket = "0-3d"
        elif age_days < 7:
            bucket = "3-7d"
        else:
            bucket = "7+d"
        bucket_counts[bucket] += 1

        # Overdue check — expected_date is in the past
        is_overdue = False
        if expected:
            try:
                exp_dt = (
                    expected
                    if isinstance(expected, datetime)
                    else datetime.fromisoformat(str(expected).replace("Z", "+00:00"))
                )
                if exp_dt.tzinfo is not None:
                    exp_dt = exp_dt.replace(tzinfo=None)
                if exp_dt < now:
                    is_overdue = True
                    overdue_count += 1
            except (ValueError, TypeError):
                pass

        tech = job.get("technician_id") or "unassigned"
        tech_counts[tech] = tech_counts.get(tech, 0) + 1

        data.append(
            {
                "job_id": job.get("job_id") or str(job.get("_id", "")),
                "job_number": job.get("job_number"),
                "order_id": job.get("order_id"),
                "status": job.get("status"),
                "technician_id": job.get("technician_id"),
                "expected_date": (
                    expected.isoformat() if isinstance(expected, datetime) else expected
                ),
                "created_at": (
                    created.isoformat() if isinstance(created, datetime) else created
                ),
                "age_days": age_days,
                "aging_bucket": bucket,
                "is_overdue": is_overdue,
            }
        )

    # Sort: oldest first, with overdue jumping to the top regardless of age.
    data.sort(key=lambda r: (not r["is_overdue"], -(r["age_days"] or 0)))

    by_technician = sorted(
        [{"technician_id": t, "count": c} for t, c in tech_counts.items()],
        key=lambda r: -r["count"],
    )

    return {
        "data": data,
        "summary": {
            "total_pending": len(data),
            "overdue": overdue_count,
            "by_aging_bucket": bucket_counts,
            "by_technician": by_technician,
        },
    }


# Roles allowed to view the workshop productivity report. A technician
# scorecard (per-staff completion / QC-fail / utilization) is a management
# lens, so it is gated to the store/area managers + admins (SUPERADMIN auto-
# passes via require_roles). Sales / cashier / workshop-staff cannot see it.
_WORKSHOP_REPORT_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")


@router.get("/workshop/productivity")
async def workshop_productivity(
    from_date: Optional[date] = Query(
        None, description="Start of the window (inclusive). Defaults to 30 days ago."
    ),
    to_date: Optional[date] = Query(
        None, description="End of the window (inclusive). Defaults to today (IST)."
    ),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_WORKSHOP_REPORT_ROLES)),
):
    """
    Workshop PRODUCTIVITY report -- per-technician utilization + QC-failure
    rate over a date range. Complements /workshop/dashboard-kpis (point-in-time
    counts) and /reports/workshop/pending-jobs (open queue) by scoring the
    technicians who DID the work in the window.

    A job is attributed to the window when it was CLOSED (completed_at) inside
    [from_date, to_date]. We score the technician who was assigned to it.

    Per-technician metrics:
        jobs_completed     -- closed jobs (COMPLETED/READY/DELIVERED) in window
        avg_turnaround_days-- mean (completed_at - created_at) across those jobs
        qc_fail_rate       -- fraction of the tech's QC'd jobs that failed at
                              least once (uses qc_history; fail-soft to qc_passed)
        on_time_rate       -- fraction completed on/before expected_date
        utilization        -- this tech's jobs_completed / busiest tech's
                              jobs_completed (0-1; relative load index)

    Plus store totals over the same window. Fail-soft: no DB / no jobs -> an
    honest empty envelope (never a fabricated average), never raises.
    """
    from database.repositories.workshop_repository import WorkshopJobRepository  # lazy

    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    # Window: default to the last 30 days ending today (IST business date).
    end_d = to_date or ist_today()
    start_d = from_date or (end_d - timedelta(days=30))
    start_str = start_d.isoformat()
    end_str = end_d.isoformat()

    empty = {
        "from_date": start_str,
        "to_date": end_str,
        "store_id": active_store,
        "technicians": [],
        "totals": {
            "jobs_completed": 0,
            "avg_turnaround_days": None,
            "qc_fail_rate": None,
            "on_time_rate": None,
            "remake_rate": None,
            "technicians_active": 0,
        },
    }

    db = get_db()
    if db is None or not getattr(db, "is_connected", True) or not active_store:
        return empty

    try:
        repo = WorkshopJobRepository(db.get_collection("workshop_jobs"))
        all_jobs = repo.find_by_store(active_store)
    except Exception:
        return empty

    def _to_dt(v):
        """Parse a stored timestamp (str or datetime) to a naive datetime, or None."""
        if not v:
            return None
        try:
            dt = (
                v
                if isinstance(v, datetime)
                else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            )
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            return None

    CLOSED = ("COMPLETED", "READY", "DELIVERED")

    # Per-technician accumulators.
    techs: Dict[str, Dict[str, Any]] = {}

    def _bucket(tech_id: str) -> Dict[str, Any]:
        key = tech_id or "unassigned"
        if key not in techs:
            techs[key] = {
                "technician_id": None if key == "unassigned" else key,
                "jobs_completed": 0,
                "_turnaround_sum": 0.0,
                "_turnaround_n": 0,
                "_qc_total": 0,
                "_qc_failed": 0,
                "_ontime_total": 0,
                "_ontime_hit": 0,
                "_remake_jobs": 0,
            }
        return techs[key]

    # Store totals.
    tot_completed = 0
    tot_turn_sum = 0.0
    tot_turn_n = 0
    tot_qc_total = 0
    tot_qc_failed = 0
    tot_ontime_total = 0
    tot_ontime_hit = 0
    tot_remake_jobs = 0

    for job in all_jobs:
        if job.get("status") not in CLOSED:
            continue
        completed = _to_dt(job.get("completed_at"))
        if completed is None:
            continue
        # Window filter on the CLOSE (completed) date.
        comp_date = completed.date()
        if comp_date < start_d or comp_date > end_d:
            continue

        b = _bucket(job.get("technician_id"))
        b["jobs_completed"] += 1
        tot_completed += 1

        # Turnaround: completed_at - created_at in days (>= 0 only).
        created = _to_dt(job.get("created_at"))
        if created is not None:
            days = (completed - created).total_seconds() / 86400.0
            if days >= 0:
                b["_turnaround_sum"] += days
                b["_turnaround_n"] += 1
                tot_turn_sum += days
                tot_turn_n += 1

        # QC outcome: a job is "QC failed" if any QC attempt in qc_history
        # failed, else fall back to the qc_passed flag. Only count jobs that
        # actually went through QC (have history or a qc_passed value).
        qc_history = [h for h in (job.get("qc_history") or []) if isinstance(h, dict)]
        had_qc = bool(qc_history) or (job.get("qc_passed") is not None)
        if had_qc:
            b["_qc_total"] += 1
            tot_qc_total += 1
            if qc_history:
                failed = any(h.get("passed") is False for h in qc_history)
            else:
                failed = job.get("qc_passed") is False
            if failed:
                b["_qc_failed"] += 1
                tot_qc_failed += 1

        # On-time: completed on/before expected_date (date-only compare).
        expected = _to_dt(job.get("expected_date"))
        if expected is not None:
            b["_ontime_total"] += 1
            tot_ontime_total += 1
            if comp_date <= expected.date():
                b["_ontime_hit"] += 1
                tot_ontime_hit += 1

        # Remake incidence (any logged remake reason on the job).
        if [e for e in (job.get("remake_reasons") or []) if isinstance(e, dict)]:
            b["_remake_jobs"] += 1
            tot_remake_jobs += 1

    # Busiest tech sets the utilization denominator (relative load index).
    max_jobs = max((t["jobs_completed"] for t in techs.values()), default=0)

    def _rate(hit, total):
        return round(hit / total, 4) if total > 0 else None

    technicians = []
    for t in techs.values():
        avg_turn = (
            round(t["_turnaround_sum"] / t["_turnaround_n"], 2)
            if t["_turnaround_n"] > 0
            else None
        )
        technicians.append(
            {
                "technician_id": t["technician_id"],
                "jobs_completed": t["jobs_completed"],
                "avg_turnaround_days": avg_turn,
                "qc_fail_rate": _rate(t["_qc_failed"], t["_qc_total"]),
                "qc_jobs": t["_qc_total"],
                "on_time_rate": _rate(t["_ontime_hit"], t["_ontime_total"]),
                "remake_jobs": t["_remake_jobs"],
                "utilization": (
                    round(t["jobs_completed"] / max_jobs, 4) if max_jobs > 0 else None
                ),
            }
        )

    technicians.sort(key=lambda r: r["jobs_completed"], reverse=True)

    # The scorecard's first column is a PERSON -- resolve technician_id to a
    # display name on the way out (these rows are constructed here, nothing is
    # stored). An id that no longer resolves gets no _name sibling and the
    # table prints the id verbatim -- never an invented name. Fail-soft.
    try:
        from ...services.name_resolver import stamp_user_names

        stamp_user_names(db, technicians, ("technician_id",))
    except Exception:  # noqa: BLE001
        pass

    return {
        "from_date": start_str,
        "to_date": end_str,
        "store_id": active_store,
        "technicians": technicians,
        "totals": {
            "jobs_completed": tot_completed,
            "avg_turnaround_days": (
                round(tot_turn_sum / tot_turn_n, 2) if tot_turn_n > 0 else None
            ),
            "qc_fail_rate": _rate(tot_qc_failed, tot_qc_total),
            "on_time_rate": _rate(tot_ontime_hit, tot_ontime_total),
            "remake_rate": _rate(tot_remake_jobs, tot_completed),
            "technicians_active": len(technicians),
        },
    }


@router.get("/stock/count")
async def daily_stock_count(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Daily stock count report"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {"data": [], "summary": {}}

    all_stock = stock_repo.find_many({"store_id": active_store}, limit=0)

    # category is on the product master, not the stock doc -> join it so the
    # per-category rows are real (FRAME etc.) instead of all under "Other".
    cat_map = _stock_category_map(all_stock)

    by_category = {}
    total_items = 0
    total_value = 0

    for item in all_stock:
        category = _row_category(item, cat_map)
        if category not in by_category:
            by_category[category] = {
                "category": category,
                "item_count": 0,
                "total_quantity": 0,
                "total_value": 0,
            }
        by_category[category]["item_count"] += 1
        by_category[category]["total_quantity"] += item.get("quantity", 0)
        by_category[category]["total_value"] += item.get("quantity", 0) * item.get(
            "cost_price", 0
        )
        total_items += 1
        total_value += item.get("quantity", 0) * item.get("cost_price", 0)

    return {
        "data": list(by_category.values()),
        "summary": {
            "total_items": total_items,
            "total_value": round(total_value, 2),
            "total_quantity": sum(item.get("quantity", 0) for item in all_stock),
        },
    }


