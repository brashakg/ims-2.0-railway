"""
IMS 2.0 - GST input-tax-credit (ITC) reconciliation
===================================================
Pure, DB-free helpers for the purchase-side GST:

  * build_itc_register(bills): the input credit available from vendor bills,
    grouped by tax period, in each bill's own stored heads (CGST/SGST/IGST).
  * reconcile_gstr2b(book_rows, gstr2b_rows): match what you booked (vendor
    bills) against GSTR-2B (what your suppliers actually reported to the GST
    portal). Buckets:
      - matched        -> safe to claim ITC
      - mismatch       -> same invoice, tax differs (investigate)
      - only_in_books  -> you have the bill but the supplier hasn't reported it
                          -> ITC AT RISK (chase the vendor; may need reversal)
      - only_in_2b     -> supplier reported it but you have no bill booked
                          -> missing purchase entry (book it, then claim)
      - booked_no_credit -> supplier reported it and the bill IS booked, but
                          it claims no credit (credit switched off, or an
                          unregistered supplier's): nothing to book, nothing
                          to claim; a tax gap shows beside it

    The sum identity (P0 #1): every rupee booked must land somewhere -- the
    return now reports itc_safe + itc_in_mismatch + itc_at_risk == total
    booked ITC. Mismatched ITC is in its own bucket (it was silently dropped
    in the pre-fix code) and adds back into total_itc.

Matching key = (supplier GSTIN, normalised invoice number, financial year of
the invoice date -- GST rule 46 lets a serial restart every 1 April), and one
2B row answers ONE booked bill. Tax amounts are compared with a small rupee
tolerance. Also flags bills older than 180 days
(the ITC-reversal window when unpaid is a separate AP concern, but old
unmatched bills are surfaced).

The finance router builds book_rows from vendor_bills joined to the vendor's
GSTIN, and gstr2b_rows from an uploaded/parsed GSTR-2B.
"""

import re
from datetime import datetime
from typing import List, Optional

# IST (TZ-P3): the as_of default must be the IST business day, not the UTC box
# clock (00:00-05:30 IST would otherwise age invoices against YESTERDAY).
from api.utils.ist import now_ist_naive

from .purchase_invoice_engine import bill_fy, split_header_tax


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _norm_inv(value) -> str:
    """Normalise an invoice number for matching: uppercase, strip spaces and
    most punctuation (suppliers and books often differ on '/', '-', leading
    zeros and case)."""
    s = str(value or "").upper()
    s = re.sub(r"[\s\-/\\.]", "", s)
    return s.lstrip("0") or s  # drop leading zeros but keep at least something


def _norm_gstin(value) -> str:
    return str(value or "").strip().upper()


def _period(date_iso) -> str:
    """YYYY-MM tax period from an ISO date; '' if unparseable."""
    s = str(date_iso or "")
    try:
        return datetime.fromisoformat(s[:10]).strftime("%Y-%m")
    except ValueError:
        return ""


def build_itc_register(bills: List[dict]) -> dict:
    """Input credit available from booked vendor bills, grouped by period.

    Every bill is reported in its STORED tax heads (cgst_total / sgst_total /
    igst_total), which every bill door writes -- so the register, GSTR-3B and
    the bill itself show one figure (F40). A legacy bill booked before its door
    stored heads is split by THE purchase classification
    (purchase_invoice_engine.split_header_tax on its own supplier and recipient
    GSTINs), never by a rule of this reader's own.

    A bill with no taxable value and no tax carries no credit and is not
    counted -- a cost-less stock transfer's Rs 0 mirror bill is not a bill.
    """
    periods: dict = {}
    total_taxable = 0.0
    total_tax = 0.0
    total_cgst = 0.0
    total_sgst = 0.0
    total_igst = 0.0
    for b in bills or []:
        if not isinstance(b, dict):
            continue
        taxable = _f(b.get("taxable_amount"))
        tax = _f(b.get("tax_amount"))
        if not taxable and not tax:
            continue
        p = _period(b.get("bill_date")) or "unknown"
        d = periods.setdefault(
            p,
            {
                "period": p,
                "taxable": 0.0,
                "tax": 0.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0,
                "bills": 0,
            },
        )
        d["taxable"] = round(d["taxable"] + taxable, 2)
        d["tax"] = round(d["tax"] + tax, 2)
        heads = (
            b
            if any(k in b for k in ("cgst_total", "sgst_total", "igst_total"))
            else split_header_tax(tax, b.get("vendor_gstin"), b.get("recipient_gstin"))
        )
        cgst_part = _f(heads.get("cgst_total"))
        sgst_part = _f(heads.get("sgst_total"))
        igst_part = _f(heads.get("igst_total"))
        d["cgst"] = round(d["cgst"] + cgst_part, 2)
        d["sgst"] = round(d["sgst"] + sgst_part, 2)
        d["igst"] = round(d["igst"] + igst_part, 2)
        total_cgst += cgst_part
        total_sgst += sgst_part
        total_igst += igst_part
        d["bills"] += 1
        total_taxable += taxable
        total_tax += tax
    return {
        "periods": sorted(periods.values(), key=lambda x: x["period"], reverse=True),
        "total_taxable": round(total_taxable, 2),
        "total_itc": round(total_tax, 2),
        "total_cgst": round(total_cgst, 2),
        "total_sgst": round(total_sgst, 2),
        "total_igst": round(total_igst, 2),
    }


def reconcile_gstr2b(
    book_rows: List[dict],
    gstr2b_rows: List[dict],
    tax_tolerance: float = 1.0,
    as_of_iso: Optional[str] = None,
    period: Optional[str] = None,
) -> dict:
    """Match booked vendor bills against GSTR-2B rows. Returns buckets + summary.

    book_rows:   [{gstin, invoice_no, taxable, tax, bill_id, vendor_name,
                   bill_date, claims_credit (default True)}]
    gstr2b_rows: [{gstin, invoice_no, taxable, tax, invoice_date (optional)}]
    period:      the 2B's return period, 'YYYY-MM' (optional).

    A bill is keyed by (GSTIN, number, financial year of its date); a 2B row
    by the same, its year read off its own invoice date, else the return
    period's (unknown on either side matches any year). A number a supplier
    reuses in a new year is a new bill, so it never answers last year's row.
    One 2B row answers ONE booked bill -- never "safe to claim" for one and
    "booked, no credit" for another.

    Within the return period: a bill dated after it cannot be in it and is
    left out; one dated before it answers its row when the supplier filed it
    late, and is otherwise not "at risk" here -- it belongs to its own
    period's 2B. Without a period every booked bill is compared.

    A booked bill that claims no credit counts in no ITC total; it answers
    its own 2B row (booked_no_credit) whatever the tax -- the supplier, number
    and year ARE the bill (the bill doors refuse a second one), so a tax gap is
    a difference on that bill, shown beside it (book_tax / portal_tax), never
    "in 2B, not booked" sending accounts to book it again.

    Sum identity (P0 #1): total booked ITC == itc_safe + itc_in_mismatch +
    itc_at_risk. The pre-fix code dropped mismatched book_tax from BOTH the
    safe bucket AND the at-risk bucket, so the three buckets didn't reconcile
    against total booked ITC. Now mismatched ITC has its own line.
    """
    as_of = None
    try:
        as_of = (
            datetime.fromisoformat((as_of_iso or "")[:10])
            if as_of_iso
            else now_ist_naive()
        )
    except ValueError:
        as_of = now_ist_naive()

    if not (period and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period)):
        period = None
    period_fy = bill_fy(f"{period}-01") if period else None

    # GSTR-2B rows by (gstin, invoice) -> every row under it, each with its year.
    b2: dict = {}
    for r in gstr2b_rows or []:
        if not isinstance(r, dict):
            continue
        key = (_norm_gstin(r.get("gstin")), _norm_inv(r.get("invoice_no")))
        b2.setdefault(key, []).append(
            {
                "gstin": r.get("gstin"),
                "invoice_no": r.get("invoice_no"),
                "taxable": _f(r.get("taxable")),
                "tax": _f(r.get("tax")),
                "fy": bill_fy(r.get("invoice_date")) if r.get("invoice_date") else period_fy,
                "seen": False,
            }
        )

    def take(key, fy):
        """The first 2B row not yet answered under `key` in year `fy` --
        marked answered."""
        for e in b2.get(key, ()):
            if e["seen"] or (fy is not None and e["fy"] is not None and fy != e["fy"]):
                continue
            e["seen"] = True
            return e
        return None

    matched, mismatch, only_books, no_credit = [], [], [], []
    itc_safe = 0.0
    itc_in_mismatch = 0.0
    itc_at_risk = 0.0
    total_book_tax = 0.0

    for row in book_rows or []:
        if not isinstance(row, dict):
            continue
        bill_date = row.get("bill_date")
        month = str(bill_date or "")[:7]
        if period and month and month > period:
            continue  # dated after the return period: cannot be in this 2B
        key = (_norm_gstin(row.get("gstin")), _norm_inv(row.get("invoice_no")))
        fy = bill_fy(bill_date)
        book_tax = _f(row.get("tax"))
        if row.get("claims_credit") is False:
            hit = take(key, fy)
            if hit:
                no_credit.append(
                    {
                        "gstin": row.get("gstin"),
                        "invoice_no": row.get("invoice_no"),
                        "vendor_name": row.get("vendor_name"),
                        "bill_id": row.get("bill_id"),
                        "bill_date": bill_date,
                        "book_tax": book_tax,
                        "portal_tax": hit["tax"],
                    }
                )
            continue
        hit = take(key, fy)
        if hit is None and period and month and month < period:
            continue  # an earlier period's bill not filed in this 2B: not this return's
        total_book_tax += book_tax
        days_old = None
        bd = None
        try:
            bd = (
                datetime.fromisoformat(str(bill_date or "")[:10]) if bill_date else None
            )
        except ValueError:
            bd = None
        if bd:
            days_old = (as_of - bd).days
        base = {
            "gstin": row.get("gstin"),
            "invoice_no": row.get("invoice_no"),
            "vendor_name": row.get("vendor_name"),
            "bill_id": row.get("bill_id"),
            "bill_date": bill_date,
            "book_tax": book_tax,
            "days_old": days_old,
        }
        if hit:
            portal_tax = hit["tax"]
            if abs(portal_tax - book_tax) <= tax_tolerance:
                matched.append({**base, "portal_tax": portal_tax})
                itc_safe += book_tax
            else:
                mismatch.append(
                    {
                        **base,
                        "portal_tax": portal_tax,
                        "diff": round(book_tax - portal_tax, 2),
                    }
                )
                itc_in_mismatch += book_tax
        else:
            only_books.append(base)
            itc_at_risk += book_tax

    only_2b = [
        {
            "gstin": e["gstin"],
            "invoice_no": e["invoice_no"],
            "taxable": e["taxable"],
            "tax": e["tax"],
        }
        for rows in b2.values()
        for e in rows
        if not e["seen"]
    ]

    return {
        "as_of": as_of.date().isoformat(),
        "period": period,
        "summary": {
            "matched": len(matched),
            "mismatch": len(mismatch),
            "only_in_books": len(only_books),
            "only_in_2b": len(only_2b),
            "booked_no_credit": len(no_credit),
            "itc_safe_to_claim": round(itc_safe, 2),
            "itc_in_mismatch": round(itc_in_mismatch, 2),
            "itc_at_risk": round(itc_at_risk, 2),
            "total_book_itc": round(total_book_tax, 2),
        },
        "matched": matched,
        "mismatch": mismatch,
        "only_in_books": sorted(only_books, key=lambda x: -(x.get("book_tax") or 0)),
        "only_in_2b": sorted(only_2b, key=lambda x: -(x.get("tax") or 0)),
        "booked_no_credit": no_credit,
    }
