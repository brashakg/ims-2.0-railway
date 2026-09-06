"""HR attendance, finance outstanding / GST summary and task reports."""

from fastapi import Depends, Query
from typing import Optional
from datetime import date, datetime, timedelta
from ...utils.ist import (
    now_ist_naive,
    ist_date_str,
    ist_day_start_utc,
)
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_order_repository,
    get_task_repository,
    get_attendance_repository,
    validate_store_access,
)
from ._shared import (
    _REPORT_FINANCE_ROLES,
    router,
)
from .gst_base import (
    _get_raw_db,
    _order_is_interstate,
    _order_taxable_and_tax,
)

# ============================================================================
# HR REPORTS
# ============================================================================


@router.get("/hr/attendance")
async def attendance_report(
    store_id: Optional[str] = Query(None),
    year: int = Query(...),
    month: int = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get attendance report for month"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    attendance_repo = get_attendance_repository()

    if attendance_repo is None:
        return {"data": [], "summary": {"total_present": 0, "total_absent": 0}}

    # Get attendance for month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    records = attendance_repo.find_many(
        {
            "store_id": active_store,
            "date": {
                "$gte": start_date.isoformat()[:10],
                "$lt": end_date.isoformat()[:10],
            },
        },
        limit=0,
    )

    return {
        "data": records,
        "summary": {
            "total_present": len([r for r in records if r.get("status") == "PRESENT"]),
            "total_absent": len([r for r in records if r.get("status") == "ABSENT"]),
        },
    }


# ============================================================================
# FINANCE REPORTS
# ============================================================================


@router.get("/finance/outstanding")
async def outstanding_report(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Get outstanding payments report"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_outstanding": 0}

    # Get orders with balance due
    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "balance_due": {"$gt": 0},
            "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]},
        },
        limit=0,
    )

    outstanding_data = []
    total = 0
    now = now_ist_naive()
    aging_buckets = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0}

    for order in orders:
        balance = order.get("balance_due", 0)
        if balance > 0:
            total += balance

            # Calculate aging bucket from order creation date
            created_str = order.get("created_at", "")
            days_old = 0
            try:
                if isinstance(created_str, str) and created_str:
                    created_dt = datetime.fromisoformat(
                        created_str.replace("Z", "+00:00").replace("+00:00", "")
                    )
                    days_old = (now - created_dt).days
                elif isinstance(created_str, datetime):
                    days_old = (now - created_str).days
            except (ValueError, TypeError):
                days_old = 0

            if days_old <= 30:
                bucket = "0-30 days"
                aging_buckets["0_30"] += balance
            elif days_old <= 60:
                bucket = "31-60 days"
                aging_buckets["31_60"] += balance
            elif days_old <= 90:
                bucket = "61-90 days"
                aging_buckets["61_90"] += balance
            else:
                bucket = "90+ days"
                aging_buckets["90_plus"] += balance

            outstanding_data.append(
                {
                    "order_id": order.get("order_id"),
                    "order_number": order.get("order_number"),
                    "customer_name": order.get("customer_name"),
                    "customer_phone": order.get("customer_phone"),
                    "total_amount": order.get("final_amount", 0),
                    "paid_amount": order.get("paid_amount", 0),
                    "balance_due": balance,
                    "created_at": order.get("created_at"),
                    "days_outstanding": days_old,
                    "aging_bucket": bucket,
                }
            )

    # Sort by age (oldest first)
    outstanding_data.sort(key=lambda x: x.get("days_outstanding", 0), reverse=True)

    return {
        "data": outstanding_data,
        "total_outstanding": total,
        "aging_summary": {
            "0-30 days": round(aging_buckets["0_30"], 2),
            "31-60 days": round(aging_buckets["31_60"], 2),
            "61-90 days": round(aging_buckets["61_90"], 2),
            "90+ days": round(aging_buckets["90_plus"], 2),
        },
        "count_by_aging": {
            "0-30 days": sum(
                1 for d in outstanding_data if d["aging_bucket"] == "0-30 days"
            ),
            "31-60 days": sum(
                1 for d in outstanding_data if d["aging_bucket"] == "31-60 days"
            ),
            "61-90 days": sum(
                1 for d in outstanding_data if d["aging_bucket"] == "61-90 days"
            ),
            "90+ days": sum(
                1 for d in outstanding_data if d["aging_bucket"] == "90+ days"
            ),
        },
    }


@router.get("/finance/gst")
async def gst_report(
    from_date: date = Query(...),
    to_date: date = Query(...),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GST report for a date range.

    Orders persist `grand_total` + `tax_amount` (and per-line taxable/tax),
    NOT `cgst_amount` / `sgst_amount` / `igst_amount` / `taxable_amount` /
    `final_amount` -- those legacy field names never landed, so the old loop
    summed all-zeros. Taxable is derived as grand_total - tax_amount (via
    _order_taxable_and_tax) and the total tax is split CGST/SGST (intra-state)
    vs IGST (inter-state) by comparing the store's home state with the
    customer's state -- the same rule GSTR-1 / GSTR-3B use.

    The `created_at` filter uses real datetime objects: BaseRepository writes
    `created_at` as a BSON datetime, so the previous `.isoformat()` STRING
    filter never matched a single order.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {
            "data": [],
            "summary": {
                "total_cgst": 0,
                "total_sgst": 0,
                "total_igst": 0,
                "total_taxable": 0,
                "total_tax": 0,
            },
        }

    # BUG-104, BOUND rule. This is a GST report a finance user reads, and it
    # must AGREE with /finance/gst-summary (finance.py), which already shifts
    # its month bounds through ist_day_start_utc: a GST tax period is an IST
    # calendar month, but created_at is a stored naive-UTC instant, so the
    # bound moves BACKWARD 5h30m. The old naive-midnight window filed an
    # invoice raised 00:00-05:30 IST on the 1st into the PREVIOUS month here
    # while the summary filed it correctly -- two GST screens, two totals,
    # same period.
    from_dt = ist_day_start_utc(from_date)
    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) - timedelta(
        microseconds=1
    )

    orders = order_repo.find_many(
        {
            "store_id": active_store,
            # Datetime objects, NOT .isoformat() strings -- created_at is a
            # BSON datetime and a string comparison never matches.
            "created_at": {"$gte": from_dt, "$lte": to_dt},
            "status": {"$nin": ["CANCELLED", "DRAFT", "cancelled", "draft", "HISTORICAL", "historical"]},
        },
        limit=0,  # 0 -> no cap: a GST report must include every invoice.
    )

    # Resolve the store's home state + a customer_id -> state map so we can
    # split intra-state (CGST+SGST) from inter-state (IGST) tax, matching
    # the GSTR-1 / GSTR-3B logic.
    store_state = ""
    cust_state_map: dict = {}
    raw_db = _get_raw_db()
    if raw_db is not None:
        try:
            store_doc = raw_db["stores"].find_one({"store_id": active_store})
            if store_doc:
                store_state = str(store_doc.get("state", "") or "")
        except Exception:
            pass
        try:
            for cust in raw_db["customers"].find({}, {"customer_id": 1, "state": 1}):
                cust_state_map[str(cust.get("customer_id", ""))] = str(
                    cust.get("state", "") or ""
                )
        except Exception:
            pass

    rows = []
    total_cgst = 0.0
    total_sgst = 0.0
    total_igst = 0.0
    total_taxable = 0.0

    for o in orders:
        taxable, tax = _order_taxable_and_tax(o)
        customer_state = (
            cust_state_map.get(str(o.get("customer_id", ""))) or store_state
        )
        # OS-008: the order's own interstate flag wins (online orders carry it);
        # store-vs-customer state stays the fallback for docs without it.
        is_inter_state = _order_is_interstate(o, store_state, customer_state)
        if is_inter_state:
            cgst = sgst = 0.0
            igst = round(tax, 2)
        else:
            cgst = sgst = round(tax / 2, 2)
            igst = 0.0

        total_cgst += cgst
        total_sgst += sgst
        total_igst += igst
        total_taxable += taxable

        rows.append(
            {
                "order_number": o.get("order_number") or o.get("order_id"),
                # THE IST CALENDAR DAY, not the stored UTC one. This is the date
                # an accountant reads off a GST row, so it is the same
                # requirement as finance.py's reconciliation row -- and it sat
                # in THIS file, two screens away from /dashboard which already
                # went through the helper. `str(...)[:10]` returned the raw
                # stored day, so an order placed 00:00-05:30 IST showed the
                # PREVIOUS day; production holds 76 such orders (8%), one of
                # them across the 1-April financial-year boundary.
                "date": ist_date_str(o.get("created_at")),
                "taxable_amount": taxable,
                "cgst": cgst,
                "sgst": sgst,
                "igst": igst,
                "total": float(o.get("grand_total", o.get("total_amount", 0)) or 0),
            }
        )

    return {
        "data": rows,
        "summary": {
            "total_taxable": round(total_taxable, 2),
            "total_cgst": round(total_cgst, 2),
            "total_sgst": round(total_sgst, 2),
            "total_igst": round(total_igst, 2),
            "total_tax": round(total_cgst + total_sgst + total_igst, 2),
        },
    }


# ============================================================================
# TASK REPORTS
# ============================================================================


@router.get("/tasks/summary")
async def task_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get task summary"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    task_repo = get_task_repository()

    if task_repo is None:
        return {
            "summary": {
                "open": 0,
                "in_progress": 0,
                "completed": 0,
                "overdue": 0,
            }
        }

    summary = task_repo.get_task_summary(active_store)
    overdue_count = task_repo.get_overdue_count(active_store)

    return {
        "summary": {
            "open": summary.get("OPEN", 0) if summary else 0,
            "in_progress": summary.get("IN_PROGRESS", 0) if summary else 0,
            "completed": summary.get("COMPLETED", 0) if summary else 0,
            "overdue": overdue_count or 0,
        }
    }


