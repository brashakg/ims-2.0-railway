"""Purchases this month (audit F56, owner ruling 2026-09-28).

One row per vendor: what we ORDERED (orders sent in the month), RECEIVED
(accepted goods at the order's price incl. GST), were BILLED, PAID, still OWE
at the month's end, and the NEXT DUE date as at the month's end. Billed / paid /
owed are the supplier ledger's own rows (ap_engine.build_ledger) over the one
row rule every payable screen reads (finance._ap_rows ->
ap_engine.supplier_ledger_rows: no transfer mirror bills; a shop's share when
one shop is asked for), so this report can never disagree with the vendor
ledger, /finance/vendor-payments, AP aging or the Cash Flow payables. Shop
scope is the one Purchase rule (resolve_store_scope).
"""

import re
from calendar import monthrange
from datetime import date

from ._shared import (
    Depends,
    HTTPException,
    Optional,
    Query,
    _AP_ROLES,
    _get_db,
    ap_engine,
    require_roles,
    resolve_store_scope,
    router,
)
from ...services import purchase_invoice_engine as pinv
from ...utils.ist import ist_date_str_from_stored, now_ist_naive

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_FIGURES = ("ordered", "received", "billed", "paid", "owed")
# Goods have been accepted into stock on a receipt in either state.
_ACCEPTED = ("ACCEPTED", "PARTIALLY_ACCEPTED")


def _month_of(value) -> str:
    """'YYYY-MM' of a stored date/instant, on the IST calendar ('' if none)."""
    return ist_date_str_from_stored(value)[:7]


def _find(db, coll: str, flt: dict) -> list:
    return list(db.get_collection(coll).find(flt, {"_id": 0}))


@router.get("/purchases-this-month")
async def purchases_this_month(
    month: Optional[str] = Query(None, description="YYYY-MM (IST); default this month"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    month = month or now_ist_naive().strftime("%Y-%m")
    if not _MONTH.match(month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    scope = resolve_store_scope(store_id, current_user)
    body = {"month": month, "store_id": scope, "vendors": [], "totals": dict.fromkeys(_FIGURES, 0.0)}
    db = _get_db()
    if db is None:
        return body

    from ..finance import _ap_rows  # the one AP row loader (call time: no cycle)

    shop = {"store_id": scope} if scope else {}
    month_end = date(int(month[:4]), int(month[5:]), monthrange(int(month[:4]), int(month[5:]))[1])
    # THE as-of day (ap_engine.as_of_day): the month's end, clamped to today
    # for the month we are in -- the day every other payable screen uses, so
    # a post-dated cheque is unpaid here exactly as it is there.
    as_of = ap_engine.as_of_day(month_end.isoformat())
    bills, payments, notes = _ap_rows(db, scope, as_of)

    rows: dict = {}

    def row(vendor_id):
        return rows.setdefault(vendor_id, dict.fromkeys(_FIGURES, 0.0))

    # ORDERED: orders sent to the vendor this month (drafts and cancelled never).
    for po in _find(
        db,
        "purchase_orders",
        {
            **({"delivery_store_id": scope} if scope else {}),
            "status": {"$nin": ["DRAFT", "CANCELLED"]},
        },
    ):
        if _month_of(po.get("sent_at") or po.get("created_at")) == month:
            row(po.get("vendor_id"))["ordered"] += float(po.get("total_amount") or po.get("total") or 0)

    # RECEIVED: goods accepted this month at the order's price incl. GST -- the
    # same lines the bill is drafted from (purchase_invoice_engine.lines_from_grn).
    # ponytail: a receipt with no order (walk-in / challan) has no order price
    # and reads 0 here until it is billed; price it from the receipt line when
    # the owner wants walk-ins counted as received.
    grns = [
        g
        for g in _find(db, "grns", {**shop, "status": {"$in": list(_ACCEPTED)}})
        if _month_of(g.get("accepted_at")) == month
    ]
    po_ids = list({g.get("po_id") for g in grns if g.get("po_id")})
    pos_by_id = {p.get("po_id"): p for p in _find(db, "purchase_orders", {"po_id": {"$in": po_ids}})}
    for g in grns:
        row(g.get("vendor_id"))["received"] += sum(
            ln["qty"] * ln["unit_price"] * (1 + ln["gst_rate"] / 100)
            for ln in pinv.lines_from_grn(g, pos_by_id.get(g.get("po_id")))
        )

    # BILLED / PAID / OWED: the supplier ledger, row for row, as it stood on the
    # as-of day (_ap_rows already dropped rows dated later; undated rows count,
    # as in the closing balance). A row's month is its ledger day's month.
    def of_vendor(docs, vid):
        return [d for d in docs if d.get("vendor_id") == vid]

    vendor_ids = {d.get("vendor_id") for d in bills + payments + notes} | set(rows)
    next_due: dict = {}
    for vid in vendor_ids:
        v_bills, v_pays, v_notes = (of_vendor(docs, vid) for docs in (bills, payments, notes))
        r = row(vid)
        for entry in ap_engine.build_ledger(v_bills, v_pays, v_notes)["entries"]:
            r["owed"] += entry["credit"] - entry["debit"]
            if ap_engine.ledger_day(entry)[:7] == month:
                if entry["type"] == "BILL":
                    r["billed"] += entry["credit"]
                elif entry["type"] == "PAYMENT":
                    r["paid"] += entry["debit"]
        # The earliest due date of a bill still owed at the month's end, after
        # on-account money has settled the oldest (ap_engine.build_aging).
        dues = [
            str(it["due_date"])[:10]  # a stored datetime and a string both compare
            for it in ap_engine.build_aging(v_bills, v_pays, v_notes, as_of)["items"]
            if it.get("due_date")
        ]
        next_due[vid] = min(dues) if dues else None

    names = {
        v.get("vendor_id"): v.get("trade_name") or v.get("legal_name") or v.get("name")
        for v in _find(db, "vendors", {"vendor_id": {"$in": list(rows)}})
    }
    for vid, r in rows.items():
        figures = {k: round(r[k], 2) for k in _FIGURES}
        if not any(figures.values()):
            continue
        body["vendors"].append(
            {
                "vendor_id": vid,
                "vendor_name": names.get(vid) or vid,
                **figures,
                "next_due_date": next_due.get(vid),
                # Past due on the day the report is as at (month end / today).
                "next_due_overdue": bool(next_due.get(vid) and next_due[vid] < as_of),
            }
        )
        for k in _FIGURES:
            body["totals"][k] = round(body["totals"][k] + figures[k], 2)
    body["vendors"].sort(key=lambda v: -v["owed"])
    return body
