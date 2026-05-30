"""
IMS 2.0 - GST input-tax-credit (ITC) reconciliation
===================================================
Pure, DB-free helpers for the purchase-side GST:

  * build_itc_register(bills): the input credit available from vendor bills,
    grouped by tax period. Detects inter-state bills via place_of_supply vs
    entity primary state and routes the tax to IGST (intra-state splits to
    CGST/SGST as before).
  * reconcile_gstr2b(book_rows, gstr2b_rows): match what you booked (vendor
    bills) against GSTR-2B (what your suppliers actually reported to the GST
    portal). Buckets:
      - matched        -> safe to claim ITC
      - mismatch       -> same invoice, tax differs (investigate)
      - only_in_books  -> you have the bill but the supplier hasn't reported it
                          -> ITC AT RISK (chase the vendor; may need reversal)
      - only_in_2b     -> supplier reported it but you have no bill booked
                          -> missing purchase entry (book it, then claim)

    The sum identity (P0 #1): every rupee booked must land somewhere -- the
    return now reports itc_safe + itc_in_mismatch + itc_at_risk == total
    booked ITC. Mismatched ITC is in its own bucket (it was silently dropped
    in the pre-fix code) and adds back into total_itc.

Matching key = (supplier GSTIN, normalised invoice number). Tax amounts are
compared with a small rupee tolerance. Also flags bills older than 180 days
(the ITC-reversal window when unpaid is a separate AP concern, but old
unmatched bills are surfaced).

The finance router builds book_rows from vendor_bills joined to the vendor's
GSTIN, and gstr2b_rows from an uploaded/parsed GSTR-2B.
"""

import re
from datetime import datetime
from typing import List, Optional


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


def _state_code(value) -> str:
    """Pull a two-digit state code from a place_of_supply or GSTIN-like value.

    Accepts '27' / '27-Maharashtra' / 'Maharashtra (27)' / a full GSTIN
    (first 2 chars). Returns '' if no two-digit prefix can be derived.
    """
    if value is None:
        return ""
    s = str(value).strip().upper()
    if not s:
        return ""
    # Numeric prefix (e.g. "27", "20-JHARKHAND", "27 Maharashtra").
    m = re.match(r"(\d{2})", s)
    if m:
        return m.group(1)
    # GSTIN: first 2 chars are the state code, then a digit/letter pattern.
    m = re.search(r"(\d{2})[A-Z]{5}\d{4}[A-Z]", s)
    if m:
        return m.group(1)
    # Parenthetical numeric (e.g. "Maharashtra (27)").
    m = re.search(r"\((\d{2})\)", s)
    if m:
        return m.group(1)
    return ""


def _is_interstate(bill_pos, entity_state) -> bool:
    """True if the bill's place_of_supply state differs from the entity's
    primary state. Missing place_of_supply -> default to intra-state (False)
    so existing bills aren't misclassified."""
    pos = _state_code(bill_pos)
    ent = _state_code(entity_state)
    if not pos or not ent:
        return False
    return pos != ent


def build_itc_register(bills: List[dict], entity_state: Optional[str] = None) -> dict:
    """Input credit available from booked vendor bills, grouped by period.

    Splits tax into CGST + SGST (intra-state) vs IGST (inter-state, when the
    bill's place_of_supply differs from `entity_state`). When place_of_supply
    is missing or entity_state is None, falls back to intra-state (CGST/SGST
    half-and-half) -- so existing data without place_of_supply behaves the
    same as before.
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
        p = _period(b.get("bill_date")) or "unknown"
        interstate = _is_interstate(b.get("place_of_supply"), entity_state)
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
        if interstate:
            d["igst"] = round(d["igst"] + tax, 2)
            total_igst += tax
        else:
            # Residual trick: compute half then assign the remainder to sgst so
            # cgst + sgst == tax exactly (avoids +-1 paisa drift on odd-paise
            # tax amounts, e.g. tax=5.01 -> half=2.50 + sgst=2.51 = 5.01).
            half = round(tax / 2, 2)
            sgst_part = round(tax - half, 2)
            d["cgst"] = round(d["cgst"] + half, 2)
            d["sgst"] = round(d["sgst"] + sgst_part, 2)
            total_cgst += half
            total_sgst += sgst_part
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
) -> dict:
    """Match booked vendor bills against GSTR-2B rows. Returns buckets + summary.

    book_rows:   [{gstin, invoice_no, taxable, tax, bill_id, vendor_name, bill_date}]
    gstr2b_rows: [{gstin, invoice_no, taxable, tax}]

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
            else datetime.utcnow()
        )
    except ValueError:
        as_of = datetime.utcnow()

    # Index GSTR-2B by (gstin, invoice).
    b2 = {}
    for r in gstr2b_rows or []:
        if not isinstance(r, dict):
            continue
        key = (_norm_gstin(r.get("gstin")), _norm_inv(r.get("invoice_no")))
        b2[key] = {
            "gstin": r.get("gstin"),
            "invoice_no": r.get("invoice_no"),
            "taxable": _f(r.get("taxable")),
            "tax": _f(r.get("tax")),
        }

    matched, mismatch, only_books = [], [], []
    seen_2b = set()
    itc_safe = 0.0
    itc_in_mismatch = 0.0
    itc_at_risk = 0.0
    total_book_tax = 0.0

    for row in book_rows or []:
        if not isinstance(row, dict):
            continue
        key = (_norm_gstin(row.get("gstin")), _norm_inv(row.get("invoice_no")))
        book_tax = _f(row.get("tax"))
        total_book_tax += book_tax
        bill_date = row.get("bill_date")
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
        if key in b2:
            seen_2b.add(key)
            portal_tax = b2[key]["tax"]
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
            "gstin": v["gstin"],
            "invoice_no": v["invoice_no"],
            "taxable": v["taxable"],
            "tax": v["tax"],
        }
        for k, v in b2.items()
        if k not in seen_2b
    ]

    return {
        "as_of": as_of.date().isoformat(),
        "summary": {
            "matched": len(matched),
            "mismatch": len(mismatch),
            "only_in_books": len(only_books),
            "only_in_2b": len(only_2b),
            "itc_safe_to_claim": round(itc_safe, 2),
            "itc_in_mismatch": round(itc_in_mismatch, 2),
            "itc_at_risk": round(itc_at_risk, 2),
            "total_book_itc": round(total_book_tax, 2),
        },
        "matched": matched,
        "mismatch": mismatch,
        "only_in_books": sorted(only_books, key=lambda x: -(x.get("book_tax") or 0)),
        "only_in_2b": sorted(only_2b, key=lambda x: -(x.get("tax") or 0)),
    }
