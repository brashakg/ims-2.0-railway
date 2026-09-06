"""Outstanding receivables (`/outstanding`) and the vendor-payments AP read.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime, timedelta
from ...utils.ist import now_ist_naive
from typing import Optional
from fastapi import Depends
from ..auth import get_current_user
from ...services import ap_engine
from ._shared import (
    UNPAID_STATUSES,
    _REAL_ORDER_STATUS_FILTER,
    _get_db,
    _order_total,
    _scope_store,
    router,
)

# === Outstanding Receivables ===


_DEFAULT_AR_CREDIT_TERMS_DAYS = 30


def _customer_credit_terms(db) -> dict:
    """customer_id -> credit_terms_days. Defaults to 30 days when missing.

    Threaded through AR so aging is computed from the due_date (= created +
    terms) rather than the create date -- a sale booked today with NET-45
    terms is NOT overdue tomorrow.
    """
    out: dict = {}
    try:
        for c in db.get_collection("customers").find(
            {},
            {
                "_id": 0,
                "customer_id": 1,
                "credit_terms_days": 1,
                "payment_terms_days": 1,
                "payment_terms": 1,
            },
        ):
            cid = c.get("customer_id")
            if not cid:
                continue
            terms = (
                c.get("credit_terms_days")
                or c.get("payment_terms_days")
                or c.get("payment_terms")
            )
            try:
                out[cid] = (
                    int(terms) if terms is not None else _DEFAULT_AR_CREDIT_TERMS_DAYS
                )
            except (TypeError, ValueError):
                out[cid] = _DEFAULT_AR_CREDIT_TERMS_DAYS
    except Exception:
        pass
    return out


def _ar_due_date(order: dict, terms_by_customer: dict) -> Optional[datetime]:
    """Compute the due date for an order: created_at + customer.credit_terms_days
    (fallback _DEFAULT_AR_CREDIT_TERMS_DAYS days when missing). Returns None when
    created_at can't be parsed."""
    created = ap_engine.parse_date(order.get("created_at"))
    if created is None:
        return None
    cid = order.get("customer_id")
    terms = terms_by_customer.get(cid)
    if terms is None:
        # Per-order override wins when a customer doc isn't in the map.
        try:
            terms = int(
                order.get("payment_terms_days") or _DEFAULT_AR_CREDIT_TERMS_DAYS
            )
        except (TypeError, ValueError):
            terms = _DEFAULT_AR_CREDIT_TERMS_DAYS
    return created + timedelta(days=int(terms))


def _ar_days_overdue(now: datetime, due: Optional[datetime]) -> int:
    """Days past the due date. <=0 means not yet due (current). None due ->
    treat as current."""
    if due is None:
        return 0
    return (now - due).days


@router.get("/outstanding")
async def get_outstanding(
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Customer receivables aged by DUE date, not order create date.

    Due date = order.created_at + customer.credit_terms_days (fallback 30).
    The old "days overdue" was actually order age which mislabels everything
    over 30 days as overdue even when the customer is within their NET-60
    terms. Real overdue = days past the due_date.
    """
    store_id = _scope_store(store_id, current_user)
    db = _get_db()
    if db is None:
        return []
    # A CANCELLED order is not a receivable even if it was left PARTIAL/UNPAID
    # before being voided -- exclude DRAFT/CANCELLED from AR.
    match = {
        "payment_status": {"$in": UNPAID_STATUSES},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_id:
        match["store_id"] = store_id

    orders = list(
        db.get_collection("orders").find(
            match,
            {
                "_id": 0,
                "order_id": 1,
                "customer_id": 1,
                "customer_name": 1,
                "customer_phone": 1,
                "total": 1,
                "grand_total": 1,
                "amount_paid": 1,
                "created_at": 1,
                "payment_terms_days": 1,
            },
        )
    )

    terms_by_customer = _customer_credit_terms(db)
    now = now_ist_naive()
    buckets = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0, "current": 0.0}
    items = []

    for o in orders:
        balance = _order_total(o) - (o.get("amount_paid", 0) or 0)
        if balance <= 0:
            continue
        due = _ar_due_date(o, terms_by_customer)
        days_overdue = _ar_days_overdue(now, due)

        if days_overdue <= 0:
            buckets["current"] += balance
        elif days_overdue <= 30:
            buckets["0_30"] += balance
        elif days_overdue <= 60:
            buckets["31_60"] += balance
        elif days_overdue <= 90:
            buckets["61_90"] += balance
        else:
            buckets["90_plus"] += balance

        cid = o.get("customer_id")
        items.append(
            {
                "order_id": o.get("order_id"),
                "customer_name": o.get("customer_name", "Unknown"),
                "customer_phone": o.get("customer_phone", ""),
                "amount": round(balance, 2),
                "days_overdue": max(0, days_overdue),
                "due_date": due.date().isoformat() if due else None,
                "payment_terms_days": terms_by_customer.get(cid)
                or o.get("payment_terms_days")
                or _DEFAULT_AR_CREDIT_TERMS_DAYS,
            }
        )

    buckets = {k: round(v, 2) for k, v in buckets.items()}
    return {
        "buckets": buckets,
        "total_outstanding": round(sum(buckets.values()), 2),
        "items": sorted(items, key=lambda x: -x["days_overdue"]),
    }


# === Vendor Payments ===


@router.get("/vendor-payments")
async def get_vendor_payments(current_user: dict = Depends(get_current_user)):
    """Per-vendor accounts-payable summary from REAL bills / payments / debit
    notes (via ap_engine). `balance` is the true outstanding payable; PO totals
    are kept only as context. Sorted by largest payable first."""
    db = _get_db()
    if db is None:
        return []
    vendors = list(
        db.get_collection("vendors").find(
            {}, {"_id": 0, "vendor_id": 1, "legal_name": 1, "trade_name": 1, "name": 1}
        )
    )

    def _grouped(coll):
        out: dict = {}
        for row in db.get_collection(coll).find({}, {"_id": 0}):
            out.setdefault(row.get("vendor_id"), []).append(row)
        return out

    bills_by_v = _grouped("vendor_bills")
    pays_by_v = _grouped("vendor_payments")
    dn_by_v = _grouped("vendor_debit_notes")

    def _po_total(p):
        return float(p.get("total_amount") or p.get("total") or 0)

    result = []
    for v in vendors:
        vid = v["vendor_id"]
        led = ap_engine.build_ledger(
            bills_by_v.get(vid, []), pays_by_v.get(vid, []), dn_by_v.get(vid, [])
        )
        pos = list(
            db.get_collection("purchase_orders").find(
                {"vendor_id": vid}, {"_id": 0, "total_amount": 1, "total": 1}
            )
        )
        po_total = round(sum(_po_total(p) for p in pos), 2)
        result.append(
            {
                "vendor_id": vid,
                "vendor_name": v.get("legal_name")
                or v.get("trade_name")
                or v.get("name")
                or vid,
                "po_total": po_total,
                "total_orders": po_total,  # back-compat alias for the old key
                "total_billed": led["total_billed"],
                "total_paid": led["total_paid"],
                "total_tds": led["total_tds"],
                "total_debit_notes": led["total_debit_notes"],
                "balance": led["closing_balance"],
            }
        )
    return sorted(result, key=lambda r: -r["balance"])
