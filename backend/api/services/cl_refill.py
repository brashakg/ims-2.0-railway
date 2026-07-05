"""IMS 2.0 - Contact-lens auto-refill (in-app trigger) service (CRM-2 phase 2).
================================================================================
The CRM router already EXPOSES a read-only per-customer refill signal
(``GET /crm/customers/{customer_id}/cl-refill-status``). The gap this module
closes: that signal was never AUTO-TRIGGERED -- nothing surfaced an actionable
worklist or created a follow-up task for staff. This service adds the in-app
path ONLY:

  * ``estimate_supply_days`` / ``compute_refill`` -- the pure refill maths,
    lifted verbatim from the existing crm.py endpoint so the per-customer view
    and the new worklist agree to the day.
  * ``scan_due_refills`` -- scan a store's recent CL orders, dedupe to the most
    recent CL line PER customer, and return everyone whose refill is due
    within a horizon (default 14 days) or already overdue.
  * the router (crm.py) turns a due row into a deduped SYSTEM task via the
    existing ``task_triggers.create_system_task`` -- the SAME engine the SLA /
    variance tasks use, so the reminder rides the existing bell + escalation.

NO outbound message is ever sent here. The customer-facing WhatsApp/SMS send
stays dark (DISPATCH_MODE-gated, owner-enabled). This is staff follow-up only.

No emoji (Windows cp1252). Fail-soft: a missing DB yields an empty worklist.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# CL categories used across the codebase (mirrors inventory.py _CL_CATEGORIES
# and the existing crm.py cl-refill-status endpoint).
CL_CATEGORIES = {
    "CONTACT_LENS",
    "CONTACT_LENSES",
    "CONTACT LENS",
    "CONTACT LENSES",
    "CL",
    "CONTACTS",
    # Colour contact lenses are contact lenses too -> same refill reminders.
    "COLORED_CONTACT_LENS",
    "COLOUR_CONTACT_LENS",
    "CCL",
}

DAILY_MODALITIES = {"DAILY", "DAILY DISPOSABLE", "1-DAY"}
MONTHLY_MODALITIES = {"MONTHLY", "MONTHLY DISPOSABLE", "30-DAY"}
BIWEEKLY_MODALITIES = {"BIWEEKLY", "2-WEEK", "FORTNIGHTLY"}

# Refill priority bands -> task priority. Overdue is the loudest follow-up.
DEFAULT_DUE_WITHIN_DAYS = 14


def estimate_supply_days(
    pack_size: int, order_qty: int, modality: str
) -> int:
    """How many days a CL purchase lasts. PURE -- lifted from crm.py so the
    per-customer endpoint and the worklist never diverge.

    Daily disposables: total lenses / 2 eyes / 1 per day.
    Monthly: each pack = 30 days (one pair per box assumed).
    Biweekly: 14 days per ordered pack. Unknown: conservative pack/2-per-day.
    """
    pack_size = int(pack_size or 0)
    order_qty = int(order_qty or 1)
    total_lenses = pack_size * order_qty
    m = (modality or "").upper()
    if m in DAILY_MODALITIES:
        return total_lenses // 2 if total_lenses >= 2 else total_lenses
    if m in MONTHLY_MODALITIES:
        return order_qty * 30
    if m in BIWEEKLY_MODALITIES:
        return order_qty * 14
    # Unknown modality: pack_size/2 per day as a conservative guess.
    return total_lenses // 2 if total_lenses >= 2 else 30


def _parse_order_dt(raw: Any) -> datetime:
    try:
        if isinstance(raw, str) and raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        if isinstance(raw, datetime):
            return raw.replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        pass
    return datetime.utcnow()


def compute_refill(
    cl_line: Dict[str, Any],
    order_date_raw: Any,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Refill prediction for ONE CL order line. PURE.

    Returns refill_due_date (ISO date), days_remaining (signed; negative =
    overdue), plus the pack/modality echo. Mirrors the crm.py endpoint exactly.
    """
    now = now or datetime.utcnow()
    pack_size = int(cl_line.get("pack_size") or cl_line.get("qty") or 0)
    modality = str(cl_line.get("modality") or cl_line.get("cl_modality") or "")
    sku = str(cl_line.get("sku") or cl_line.get("product_id") or "")
    order_qty = int(cl_line.get("quantity") or cl_line.get("return_qty") or 1)

    supply_days = estimate_supply_days(pack_size, order_qty, modality)
    order_dt = _parse_order_dt(order_date_raw)
    refill_due = order_dt + timedelta(days=max(supply_days, 1))
    days_remaining = (refill_due - now).days

    return {
        "refill_due_date": refill_due.date().isoformat(),
        "days_remaining": days_remaining,
        "last_cl_order_date": order_dt.date().isoformat(),
        "sku": sku or None,
        "modality": modality or None,
        "pack_size": pack_size or None,
    }


def _is_cl_line(item: Dict[str, Any]) -> bool:
    cat = str(item.get("category") or item.get("item_type") or "").upper()
    return cat in CL_CATEGORIES


def scan_due_refills(
    db,
    store_id: Optional[str] = None,
    *,
    due_within_days: int = DEFAULT_DUE_WITHIN_DAYS,
    scan_limit: int = 2000,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Worklist of customers whose CL refill is DUE (<= horizon) or overdue.

    Scans recent orders (optionally store-scoped), keeps the most-recent CL
    line PER customer (orders are scanned newest-first; the first CL hit per
    customer wins), computes the refill, and keeps rows where
    days_remaining <= due_within_days. Sorted most-overdue first.

    Fail-soft: db None / no orders collection -> []. Never raises.
    """
    if db is None:
        return []
    now = now or datetime.utcnow()
    try:
        orders_coll = db.get_collection("orders")
    except Exception:  # noqa: BLE001
        orders_coll = None
    if orders_coll is None:
        return []

    query: Dict[str, Any] = {}
    if store_id:
        # Orders record the store under different keys across sources; OR them
        # so no store order is missed (mirrors the CRM customer store-scope).
        query["$or"] = [
            {"store_id": store_id},
            {"home_store_id": store_id},
            {"preferred_store_id": store_id},
        ]

    try:
        cursor = (
            orders_coll.find(
                query,
                {
                    "_id": 0,
                    "order_id": 1,
                    "customer_id": 1,
                    "customer_name": 1,
                    "created_at": 1,
                    "order_date": 1,
                    "items": 1,
                },
            )
            .sort("created_at", -1)
            .limit(int(scan_limit))
        )
        orders = list(cursor)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CL-REFILL] scan failed: %s", exc)
        return []

    seen: set = set()
    out: List[Dict[str, Any]] = []
    for order in orders:
        cid = order.get("customer_id")
        if not cid or cid in seen:
            continue
        cl_line = None
        for item in order.get("items") or []:
            if _is_cl_line(item):
                cl_line = item
                break
        if cl_line is None:
            continue
        # First (newest) CL order for this customer is authoritative.
        seen.add(cid)
        order_date_raw = order.get("created_at") or order.get("order_date")
        refill = compute_refill(cl_line, order_date_raw, now=now)
        if refill["days_remaining"] > int(due_within_days):
            continue
        out.append(
            {
                "customer_id": cid,
                "customer_name": order.get("customer_name"),
                "last_cl_order_id": order.get("order_id"),
                "overdue": refill["days_remaining"] < 0,
                **refill,
            }
        )

    out.sort(key=lambda r: r.get("days_remaining", 0))
    return out


def refill_task_priority(days_remaining: int) -> str:
    """Task priority for a due refill. Overdue is a P2 (real, time-bound
    follow-up); merely upcoming is a P3."""
    return "P2" if int(days_remaining) < 0 else "P3"
