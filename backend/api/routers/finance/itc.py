"""GST input-tax-credit register, GSTR-2B reconcile and the ITC CSV export.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

import io
from typing import Optional, List, Dict
from fastapi import Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from ..auth import get_current_user
from ...services import itc_reconcile, csv_safe
from ._shared import _get_db, _require_finance_admin, router
from .gst import _itc_eligible_bill

# === GST input-tax-credit (ITC) reconciliation (ADMIN / ACCOUNTANT) ===


def _primary_entity_state(db, entity_id: Optional[str] = None) -> Optional[str]:
    """Resolve the primary state code for the entity.

    When `entity_id` is given, take that entity's `primary_state` / `state`.
    Otherwise pick the first entity in the DB. Returns None when no entity
    matches -- in that case the ITC register falls back to intra-state
    behaviour (existing rows aren't reclassified).
    """
    try:
        coll = db.get_collection("entities")
        if entity_id:
            doc = coll.find_one(
                {"entity_id": entity_id},
                {"_id": 0, "primary_state": 1, "state": 1, "state_code": 1},
            )
        else:
            doc = coll.find_one(
                {}, {"_id": 0, "primary_state": 1, "state": 1, "state_code": 1}
            )
        if not doc:
            return None
        return (
            doc.get("primary_state")
            or doc.get("state_code")
            or doc.get("state")
            or None
        )
    except Exception:
        return None


@router.get("/itc-register")
async def itc_register(
    period: Optional[str] = Query(None, description="YYYY-MM filter; omit for all"),
    entity_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Input tax credit available from booked vendor bills, grouped by period.

    When `period` (YYYY-MM) is given, only that period is returned in
    `periods[]` (totals still represent the full bill set so the FE can show
    a "Total booked ITC" anchor)."""
    _require_finance_admin(current_user)
    db = _get_db()
    if db is None:
        return {
            "periods": [],
            "total_taxable": 0,
            "total_itc": 0,
            "total_cgst": 0,
            "total_sgst": 0,
            "total_igst": 0,
        }
    # Scope like the GSTR-3B Table-4 ITC read (reports._itc_from_vendor_bills,
    # #899): (1) when entity_id is given, count only THAT entity's booked bills
    # via recipient_entity_id -- the same field a transfer mirror bill records
    # for its RECEIVING entity, so an inter-entity/same-entity-cross-state
    # transfer's ITC lands with the receiver and never inflates the sender's
    # register; (2) always drop ITC-ineligible bills (cancelled / 17(5)-blocked
    # / not-yet-received) via _itc_eligible_bill -- the register previously
    # summed EVERY vendor_bills doc, so cancelled + ineligible tax showed as
    # claimable ITC. The projection widens to carry the eligibility + scoping
    # fields; the output shape is unchanged.
    q: dict = {}
    if entity_id:
        q["recipient_entity_id"] = entity_id
    try:
        raw_bills = list(
            db.get_collection("vendor_bills").find(
                q,
                {
                    "_id": 0,
                    "bill_date": 1,
                    "taxable_amount": 1,
                    "tax_amount": 1,
                    "place_of_supply": 1,
                    "status": 1,
                    "itc_blocked": 1,
                    "itc_eligible": 1,
                    "received": 1,
                },
            )
        )
    except Exception:
        raw_bills = []
    bills = [b for b in raw_bills if _itc_eligible_bill(b)]
    entity_state = _primary_entity_state(db, entity_id)
    out = itc_reconcile.build_itc_register(bills, entity_state=entity_state)
    if period:
        out["periods"] = [p for p in out["periods"] if p.get("period") == period]
    return out


class Gstr2bRow(BaseModel):
    gstin: Optional[str] = None
    invoice_no: Optional[str] = None
    taxable: Optional[float] = 0
    tax: Optional[float] = 0


class Gstr2bReconcileBody(BaseModel):
    rows: List[Gstr2bRow] = Field(default_factory=list)
    as_of: Optional[str] = None


def _book_rows_from_db(db) -> List[dict]:
    """Pull all vendor bills + their vendor GSTIN, formatted for the reconciler."""
    gstin_by_vendor: Dict[str, str] = {}
    try:
        for v in db.get_collection("vendors").find(
            {}, {"_id": 0, "vendor_id": 1, "gstin": 1}
        ):
            gstin_by_vendor[v.get("vendor_id")] = v.get("gstin")
    except Exception:
        pass
    rows = []
    try:
        for b in db.get_collection("vendor_bills").find({}, {"_id": 0}):
            rows.append(
                {
                    "gstin": gstin_by_vendor.get(b.get("vendor_id")),
                    "invoice_no": b.get("bill_number"),
                    "taxable": b.get("taxable_amount"),
                    "tax": b.get("tax_amount"),
                    "bill_id": b.get("bill_id"),
                    "vendor_name": b.get("vendor_name"),
                    "bill_date": b.get("bill_date"),
                    "place_of_supply": b.get("place_of_supply"),
                }
            )
    except Exception:
        pass
    return rows


@router.post("/gstr2b-reconcile")
async def gstr2b_reconcile(
    body: Gstr2bReconcileBody, current_user: dict = Depends(get_current_user)
):
    """Reconcile booked vendor bills against an uploaded GSTR-2B (rows parsed
    client-side from the portal download). Returns matched / mismatch /
    only-in-books (ITC at risk) / only-in-2B buckets, plus a sum-identity
    summary (matched + mismatch + at-risk == total booked ITC)."""
    _require_finance_admin(current_user)
    rows = [r.model_dump() for r in body.rows]
    db = _get_db()
    if db is None:
        return itc_reconcile.reconcile_gstr2b([], rows, as_of_iso=body.as_of)
    return itc_reconcile.reconcile_gstr2b(
        _book_rows_from_db(db), rows, as_of_iso=body.as_of
    )


_ITC_CSV_HEADERS = {
    "matched": [
        "vendor_name",
        "gstin",
        "invoice_no",
        "bill_date",
        "book_tax",
        "portal_tax",
    ],
    "mismatch": [
        "vendor_name",
        "gstin",
        "invoice_no",
        "bill_date",
        "book_tax",
        "portal_tax",
        "diff",
    ],
    "only_in_books": [
        "vendor_name",
        "gstin",
        "invoice_no",
        "bill_date",
        "book_tax",
        "days_old",
    ],
    "only_in_2b": ["gstin", "invoice_no", "taxable", "tax"],
}


@router.post("/itc-export")
async def itc_export_csv(
    body: Gstr2bReconcileBody,
    bucket: str = Query(..., pattern="^(matched|mismatch|only_in_books|only_in_2b)$"),
    current_user: dict = Depends(get_current_user),
):
    """CSV export of a single reconciliation bucket. POST instead of GET
    because the GSTR-2B rows live client-side (the FE keeps the upload in
    memory; re-uploading on every download would be terrible UX)."""
    _require_finance_admin(current_user)
    rows = [r.model_dump() for r in body.rows]
    db = _get_db()
    book_rows = _book_rows_from_db(db) if db is not None else []
    recon = itc_reconcile.reconcile_gstr2b(book_rows, rows, as_of_iso=body.as_of)
    bucket_rows = recon.get(bucket) or []
    headers = _ITC_CSV_HEADERS[bucket]

    buf = io.StringIO()
    # BUG-139: neutralize formula-injection -- the GSTR-2B rows are uploaded
    # client-side, so vendor_name/gstin/invoice_no are fully attacker-controlled.
    writer = csv_safe.safe_writer(buf)
    writer.writerow(headers)
    for r in bucket_rows:
        writer.writerow([r.get(h, "") for h in headers])
    csv_bytes = (csv_safe.BOM + buf.getvalue()).encode("utf-8")
    fname = f"itc_{bucket}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
