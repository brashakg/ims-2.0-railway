"""
IMS 2.0 - PO vs GRN Variance + Backorder engine (Feature F8)
============================================================
Pure, side-effect-free helpers that close the procurement accountability loop:
every quantity discrepancy between what was ORDERED (purchase order) and what
was RECEIVED (goods-receipt notes) must be surfaced -- not silently absorbed --
and backordered goods must be flagged before they become forgotten liabilities.

NO DB imports here. The router fetches the PO + its GRNs and calls these
helpers, so the variance math, the aging classification, and the
aged-backorder task-spec generation are all trivially unit-testable without a
database (which is exactly what makes the F8 acceptance tests deterministic in
CI -- there is no fail-soft "no Mongo" branch in this module to diverge).

KEY CONCEPTS
------------
* open_qty   = ordered_qty - received_qty (clamped at 0; never negative even
               on an over-receipt).
* received_qty here = the cumulative ACCEPTED qty across every ACCEPTED GRN for
               the PO (an un-accepted / pending / disputed GRN has not really
               entered stock, so it does NOT close out the order line).
* aging_status is an EXPLICIT ENUM, never a colour string (PROTOCOL P1: every
               legacy Excel colour-flag becomes a status enum field):
                 ON_TIME             -- not past expected_date.
                 OVERDUE             -- 1..(threshold-1) days past.
                 CRITICALLY_OVERDUE  -- threshold (default 14) or more days past.
* variance_status reuses the existing GRN line classification
               (SHORT / OVER / EXACT / UNMATCHED) computed against the ordered
               qty so the report agrees with what the GRN doc already stamped.

This module does NOT mutate orders, POS, payments, or AP balances. It only
reads PO/GRN dicts and returns computed views + task specs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

# Aging enum values (explicit status, not colour).
AGING_ON_TIME = "ON_TIME"
AGING_OVERDUE = "OVERDUE"
AGING_CRITICALLY_OVERDUE = "CRITICALLY_OVERDUE"

# GRN statuses that count as having physically entered stock. A receipt only
# closes out an ordered line once it is ACCEPTED.
_ACCEPTED_GRN_STATUSES = {"ACCEPTED"}

# Default day-count past expected_date at which an open backorder is escalated
# from OVERDUE to CRITICALLY_OVERDUE (and its task from P2 to P1).
DEFAULT_CRITICAL_THRESHOLD_DAYS = 14


def _int(v: Any) -> int:
    """Coerce to int; garbage/None -> 0 so nothing here ever raises."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _classify_line_variance(received_qty: int, ordered_qty: Optional[int]) -> str:
    """SHORT / OVER / EXACT / UNMATCHED for a received-vs-ordered line.

    Mirrors vendors.classify_grn_line_variance but kept local so this engine has
    ZERO imports from a router (pure + independently testable). ordered_qty None
    -> UNMATCHED (nothing to compare against).
    """
    if ordered_qty is None:
        return "UNMATCHED"
    delta = _int(received_qty) - _int(ordered_qty)
    if delta < 0:
        return "SHORT"
    if delta > 0:
        return "OVER"
    return "EXACT"


def _parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort parse of an ISO date/datetime string or datetime object.

    Returns a naive datetime (tz stripped) or None. Total: never raises.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value:
        try:
            cleaned = value.replace("Z", "").split("+")[0].strip()
            return datetime.fromisoformat(cleaned)
        except ValueError:
            # Date-only fallback (YYYY-MM-DD already handled by fromisoformat;
            # this catches odd separators).
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                return None
    return None


def days_overdue(expected_date: Any, now: Optional[datetime] = None) -> int:
    """Whole days the expected_date is in the past (0 if today/future/unknown).

    Pure + total. A missing or unparseable expected_date yields 0 (treated as
    not-yet-due rather than infinitely overdue).
    """
    now = now or datetime.now()
    exp = _parse_dt(expected_date)
    if exp is None:
        return 0
    delta_days = (now - exp).days
    return delta_days if delta_days > 0 else 0


def aging_status(
    expected_date: Any,
    now: Optional[datetime] = None,
    threshold_days: int = DEFAULT_CRITICAL_THRESHOLD_DAYS,
) -> str:
    """Classify a PO line's lateness as an ENUM (never a colour).

    ON_TIME            : not past expected_date.
    OVERDUE            : 1..(threshold-1) days past.
    CRITICALLY_OVERDUE : threshold+ days past.
    """
    od = days_overdue(expected_date, now=now)
    if od <= 0:
        return AGING_ON_TIME
    if od >= max(1, int(threshold_days)):
        return AGING_CRITICALLY_OVERDUE
    return AGING_OVERDUE


def _ordered_by_product(po: Dict[str, Any]) -> Dict[str, int]:
    """Sum ordered quantity per product across the PO's line items."""
    ordered: Dict[str, int] = {}
    items = po.get("items") if isinstance(po, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        pid = item.get("product_id")
        if pid is None:
            continue
        ordered[pid] = ordered.get(pid, 0) + _int(item.get("quantity"))
    return ordered


def _product_name_map(po: Dict[str, Any]) -> Dict[str, str]:
    """product_id -> a human label (product_name or sku) from the PO lines."""
    names: Dict[str, str] = {}
    for item in (po.get("items") or []) if isinstance(po, dict) else []:
        if not isinstance(item, dict):
            continue
        pid = item.get("product_id")
        if pid is None or pid in names:
            continue
        names[pid] = item.get("product_name") or item.get("sku") or str(pid)
    return names


def _received_accepted_rejected(grns: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Aggregate per-product received/accepted/rejected across ACCEPTED GRNs.

    Only ACCEPTED GRNs count -- a pending/disputed receipt has not entered stock
    and must NOT close out an open order line (otherwise a disputed delivery
    would hide a genuine shortfall).
    """
    agg: Dict[str, Dict[str, int]] = {}
    for grn in grns or []:
        if not isinstance(grn, dict):
            continue
        status = str(grn.get("status", "") or "").upper()
        if status not in _ACCEPTED_GRN_STATUSES:
            continue
        for item in grn.get("items") or []:
            if not isinstance(item, dict):
                continue
            pid = item.get("product_id")
            if pid is None:
                continue
            bucket = agg.setdefault(
                pid, {"received_qty": 0, "accepted_qty": 0, "rejected_qty": 0}
            )
            bucket["received_qty"] += _int(item.get("received_qty"))
            bucket["accepted_qty"] += _int(item.get("accepted_qty"))
            bucket["rejected_qty"] += _int(item.get("rejected_qty"))
    return agg


def _latest_accepted_grn_by_product(
    grns: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """product_id -> grn_id of the most recent ACCEPTED GRN carrying it. PURE.

    "Most recent" prefers created_at (parseable string or datetime); for ties /
    unparseable timestamps the later-in-list GRN wins. Non-ACCEPTED GRNs are
    ignored (same rule as the qty aggregation). This is what lets the variance
    report carry `latest_accepted_grn_id` per line so the dismiss flow can pass
    grn_id+bill_id and the debit-note prompt actually fires.
    """
    latest: Dict[str, Any] = {}  # pid -> ((created_at, idx), grn_id)
    for idx, grn in enumerate(grns or []):
        if not isinstance(grn, dict):
            continue
        if str(grn.get("status", "") or "").upper() not in _ACCEPTED_GRN_STATUSES:
            continue
        gid = grn.get("grn_id")
        if gid is None:
            continue
        key = (_parse_dt(grn.get("created_at")) or datetime.min, idx)
        for item in grn.get("items") or []:
            if not isinstance(item, dict):
                continue
            pid = item.get("product_id")
            if pid is None:
                continue
            prev = latest.get(pid)
            if prev is None or key >= prev[0]:
                latest[pid] = (key, gid)
    return {pid: v[1] for pid, v in latest.items()}


def _dismissed_product_ids(po: Dict[str, Any]) -> set:
    """The set of product_ids whose variance has been dismissed on this PO."""
    out = set()
    for entry in (po.get("dismissed_variances") or []) if isinstance(po, dict) else []:
        if isinstance(entry, dict) and entry.get("product_id") is not None:
            out.add(entry.get("product_id"))
    return out


def open_qty_per_line(
    po: Dict[str, Any],
    grns: List[Dict[str, Any]],
    now: Optional[datetime] = None,
    threshold_days: int = DEFAULT_CRITICAL_THRESHOLD_DAYS,
) -> List[Dict[str, Any]]:
    """Per-product variance view for one PO against its GRNs. PURE.

    Returns one row per ordered product:
        {
          po_id, po_number, vendor_id, vendor_name,
          product_id, product_name,
          ordered_qty, received_qty, open_qty,
          accepted_qty, rejected_qty,
          variance_status,            # SHORT/OVER/EXACT/UNMATCHED
          days_overdue, aging_status, # explicit enum, never a colour
          dismissed,                  # True if this line was dismissed
          latest_accepted_grn_id      # newest ACCEPTED GRN with this product
        }

    open_qty = max(ordered - received, 0). received = cumulative ACCEPTED qty.
    """
    if not isinstance(po, dict):
        return []

    ordered = _ordered_by_product(po)
    names = _product_name_map(po)
    received_agg = _received_accepted_rejected(grns)
    dismissed = _dismissed_product_ids(po)
    latest_grn = _latest_accepted_grn_by_product(grns)

    expected_date = po.get("expected_date")
    od = days_overdue(expected_date, now=now)
    aging = aging_status(expected_date, now=now, threshold_days=threshold_days)

    po_id = po.get("po_id")
    po_number = po.get("po_number")
    vendor_id = po.get("vendor_id")
    vendor_name = po.get("vendor_name")

    rows: List[Dict[str, Any]] = []
    for pid, ordered_qty in ordered.items():
        agg = received_agg.get(pid) or {}
        received_qty = _int(agg.get("received_qty"))
        accepted_qty = _int(agg.get("accepted_qty"))
        rejected_qty = _int(agg.get("rejected_qty"))
        # Open qty is measured against ACCEPTED units: a rejected unit did not
        # satisfy the order, so it stays open.
        open_qty = ordered_qty - accepted_qty
        if open_qty < 0:
            open_qty = 0
        rows.append(
            {
                "po_id": po_id,
                "po_number": po_number,
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "product_id": pid,
                "product_name": names.get(pid, str(pid)),
                "ordered_qty": ordered_qty,
                "received_qty": received_qty,
                "accepted_qty": accepted_qty,
                "rejected_qty": rejected_qty,
                "open_qty": open_qty,
                "variance_status": _classify_line_variance(accepted_qty, ordered_qty),
                "days_overdue": od,
                "aging_status": aging,
                "dismissed": pid in dismissed,
                # The newest ACCEPTED GRN covering this product (None when the
                # line has no accepted receipt yet). Carried so the dismiss
                # call can link grn_id and reach the debit-note suggestion.
                "latest_accepted_grn_id": latest_grn.get(pid),
            }
        )
    return rows


def variance_report_lines(
    pos: List[Dict[str, Any]],
    grns_by_po: Dict[str, List[Dict[str, Any]]],
    now: Optional[datetime] = None,
    threshold_days: int = DEFAULT_CRITICAL_THRESHOLD_DAYS,
    include_dismissed: bool = False,
) -> List[Dict[str, Any]]:
    """Flatten open_qty_per_line across many POs into a single report list.

    A fully-satisfied line (open_qty == 0 AND variance_status == EXACT) is
    OMITTED -- there is nothing to show. An over-receipt (OVER) is kept even
    when open_qty is 0 because an over-shipment is itself a discrepancy worth
    surfacing. Dismissed lines are hidden unless include_dismissed is True.
    """
    out: List[Dict[str, Any]] = []
    for po in pos or []:
        if not isinstance(po, dict):
            continue
        po_id = po.get("po_id")
        grns = grns_by_po.get(po_id, []) if po_id else []
        for row in open_qty_per_line(
            po, grns, now=now, threshold_days=threshold_days
        ):
            if row.get("dismissed") and not include_dismissed:
                continue
            if row.get("open_qty", 0) == 0 and row.get("variance_status") == "EXACT":
                continue
            out.append(row)
    return out


def aged_backorder_tasks_needed(
    pos: List[Dict[str, Any]],
    grns_by_po: Dict[str, List[Dict[str, Any]]],
    now: Optional[datetime] = None,
    threshold_days: int = DEFAULT_CRITICAL_THRESHOLD_DAYS,
) -> List[Dict[str, Any]]:
    """Task specs for the TASKMASTER aged-backorder sweep. PURE.

    For every OPEN line (open_qty > 0) on a PO that is past its expected_date,
    emit a spec:
        {
          po_id, po_number, vendor_id, vendor_name,
          product_id, product_name,
          open_qty, days_overdue, aging_status,
          priority,                      # P1 if CRITICALLY_OVERDUE else P2
          source_ref,                    # "backorder:{po_id}:{product_id}"
          escalate                       # True once CRITICALLY_OVERDUE (P2->P1)
        }

    Dismissed lines are SKIPPED -- an Admin decided not to chase them. Lines that
    are not yet overdue (aging ON_TIME) are skipped (nothing to chase yet).
    """
    specs: List[Dict[str, Any]] = []
    for po in pos or []:
        if not isinstance(po, dict):
            continue
        po_id = po.get("po_id")
        grns = grns_by_po.get(po_id, []) if po_id else []
        for row in open_qty_per_line(
            po, grns, now=now, threshold_days=threshold_days
        ):
            if row.get("dismissed"):
                continue
            if row.get("open_qty", 0) <= 0:
                continue
            aging = row.get("aging_status")
            if aging == AGING_ON_TIME:
                continue
            critical = aging == AGING_CRITICALLY_OVERDUE
            specs.append(
                {
                    "po_id": row.get("po_id"),
                    "po_number": row.get("po_number"),
                    "vendor_id": row.get("vendor_id"),
                    "vendor_name": row.get("vendor_name"),
                    "product_id": row.get("product_id"),
                    "product_name": row.get("product_name"),
                    "open_qty": row.get("open_qty"),
                    "days_overdue": row.get("days_overdue"),
                    "aging_status": aging,
                    "priority": "P1" if critical else "P2",
                    "source_ref": f"backorder:{row.get('po_id')}:{row.get('product_id')}",
                    "escalate": critical,
                }
            )
    return specs
