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
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# THE contact-lens categories for refill and the CL reorder segment. The
# per-customer status (crm.py), the store worklist and the campaign segment all
# read this set; never a local copy (L1-F19).
CL_CATEGORIES = {
    "CONTACT_LENS",
    "CONTACT_LENSES",
    "CONTACT LENS",
    "CONTACT LENSES",
    "CL",
    "CONTACTS",
    "CONTACTLENS",
    # Colour contact lenses are contact lenses too -> same refill reminders.
    "COLORED_CONTACT_LENS",
    "COLOUR_CONTACT_LENS",
    "CCL",
}

DAILY_MODALITIES = {"DAILY", "DAILY DISPOSABLE", "1-DAY"}
MONTHLY_MODALITIES = {"MONTHLY", "MONTHLY DISPOSABLE", "30-DAY"}
BIWEEKLY_MODALITIES = {"BIWEEKLY", "2-WEEK", "FORTNIGHTLY"}

# L1-F18 + D50: only goods the customer RECEIVED are supply, so only received
# evidence restarts the refill clock: DELIVERED (courier / counter hand-over),
# COMPLETED, COLLECTED, and HISTORICAL (a pre-IMS order imported for customer
# history: the customer DID receive those lenses). Never SHIPPED (still with
# the courier), PAID (money, not goods), FULFILLED (Shopify's word for
# shipped), a draft / quote, a cancelled / refunded / void order, or one still
# being made (CONFIRMED / PROCESSING / READY). Any case (imports wrote any).
RECEIVED_STATUSES = ("DELIVERED", "COMPLETED", "COLLECTED", "HISTORICAL")
SUPPLIED_STATUSES = tuple(
    v for s in RECEIVED_STATUSES for v in (s, s.lower(), s.title())
)

# The CL-line match as a Mongo filter, so the order reads select CL supply
# BEFORE their limit (Codex PR-1428 r1: 50 newer frame orders hid an overdue
# CL order from the per-customer door). Exact category, any case; the Python
# rule (_is_cl_line) still decides each line.
_CL_RE = "^(" + "|".join(re.escape(c) for c in sorted(CL_CATEGORIES)) + ")$"
CL_ORDER_FILTER = {
    "$or": [
        {"items.category": {"$regex": _CL_RE, "$options": "i"}},
        {"items.item_type": {"$regex": _CL_RE, "$options": "i"}},
    ]
}

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


def supplied_cl_lines(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every contact-lens line this order actually left with the customer.
    THE supply rule for every refill door (L1-F18, D50).

    The order must be a received sale (SUPPLIED_STATUSES). Returned boxes are
    not supply: each line's quantity is reduced by its ``returned_qty`` (the
    count the Returns door increments), and a fully returned line is dropped.
    Pure."""
    if order.get("status") not in SUPPLIED_STATUSES:
        return []
    out = []
    for item in order.get("items") or []:
        if not _is_cl_line(item):
            continue
        try:
            qty = float(item.get("quantity") or 1)
            returned = float(item.get("returned_qty") or 0)
        except (TypeError, ValueError):
            qty, returned = 1.0, 0.0
        kept = int(qty - returned)
        if kept > 0:
            out.append({**item, "quantity": kept})
    return out


def latest_supplies(
    orders_coll, query: Dict[str, Any], limit: int
) -> List[tuple]:
    """(order, supplied CL line) pairs -- one per kept CL line -- newest order
    first, for the orders matching ``query``. Only received orders WITH a CL
    line are read (status and CL filter are in the query, so frame sales and
    cancelled rows never use up the limit). RAISES when the orders cannot be
    read: an unreadable history is not "no history"."""
    cursor = (
        orders_coll.find(
            {
                **query,
                "$and": [CL_ORDER_FILTER],
                "status": {"$in": list(SUPPLIED_STATUSES)},
            },
            {
                "_id": 0,
                "order_id": 1,
                "customer_id": 1,
                "customer_name": 1,
                "created_at": 1,
                "order_date": 1,
                "status": 1,
                "items": 1,
            },
        )
        .sort("created_at", -1)
        .limit(int(limit))
    )
    out = []
    for order in cursor:
        for line in supplied_cl_lines(order):
            out.append((order, line))
    return out


def _clock_key(line: Dict[str, Any]) -> tuple:
    """Which refill clock a line feeds (D50): one per product and eye."""
    product = str(
        line.get("sku") or line.get("product_id") or line.get("product_name")
        or line.get("name") or ""
    ).strip().upper()
    eye = str(line.get("eye") or line.get("side") or "").strip().upper()
    return (product, eye)


def earliest_refill(
    supplies: List[tuple], *, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """THE refill for one customer, from their (order, line) supplies newest
    first (D50). Each product / eye keeps its own clock from its LATEST
    received line (an older line for the same clock is ignored; lines of the
    same order all count); the EARLIEST-due clock drives the reminder. None
    when there is no supply. Pure."""
    now = now or datetime.utcnow()
    clock_order: Dict[tuple, int] = {}
    best: Optional[Dict[str, Any]] = None
    for order, line in supplies:
        key = _clock_key(line)
        if clock_order.setdefault(key, id(order)) != id(order):
            continue  # an older supply of a clock a newer order already set
        refill = compute_refill(
            line, order.get("created_at") or order.get("order_date"), now=now
        )
        if best is None or refill["days_remaining"] < best["days_remaining"]:
            best = {
                **refill,
                "last_cl_order_id": order.get("order_id"),
                "customer_name": order.get("customer_name"),
            }
    return best


def scan_due_refills(
    db,
    store_id: Optional[str] = None,
    *,
    due_within_days: int = DEFAULT_DUE_WITHIN_DAYS,
    scan_limit: int = 2000,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Worklist of customers whose CL refill is DUE (<= horizon) or overdue.

    Scans recent RECEIVED CL orders (optionally store-scoped; latest_supplies),
    groups them per customer, takes THE refill (earliest_refill: per product /
    eye clock, earliest due drives) and keeps rows where days_remaining <=
    due_within_days. Sorted most-overdue first.

    db None -> []. An orders read that FAILS raises (the router turns it into
    a 503): an empty worklist must mean nobody is due, not "could not read".
    """
    if db is None:
        return []
    now = now or datetime.utcnow()
    orders_coll = db.get_collection("orders")

    query: Dict[str, Any] = {}
    if store_id:
        # Orders record the store under different keys across sources; OR them
        # so no store order is missed (mirrors the CRM customer store-scope).
        query["$or"] = [
            {"store_id": store_id},
            {"home_store_id": store_id},
            {"preferred_store_id": store_id},
        ]

    # Raises on a read failure: the router answers 503, never an empty list
    # that looks like "nobody is due" (L1-F19).
    supplies = latest_supplies(orders_coll, query, scan_limit)

    by_customer: Dict[str, List[tuple]] = {}
    for order, cl_line in supplies:
        cid = order.get("customer_id")
        if cid:
            by_customer.setdefault(cid, []).append((order, cl_line))

    out: List[Dict[str, Any]] = []
    for cid, rows in by_customer.items():
        refill = earliest_refill(rows, now=now)
        if refill is None or refill["days_remaining"] > int(due_within_days):
            continue
        out.append(
            {
                "customer_id": cid,
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
