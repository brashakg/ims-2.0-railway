"""RTV (Return-To-Vendor) Debit Note -- GST-compliant accounting document.

Feature #20. A DEBIT NOTE is the formal GST document issued to a vendor when
goods are returned to them (the physical RTV / N4 RMA / vendor_return already
moved the stock). The debit note DEBITS the vendor: it reduces accounts payable
and lets the business reverse the input-tax-credit claimed on the original
purchase. This module is a pure DOCUMENT / accounting layer that sits on top of
the existing vendor-return / RMA. It does NOT touch the RMA state machine nor
the stock movement.

Pure builders only -- no DB access here (the router seeds the serial + persists).

Debit-note document shape (returned by ``build_debit_note``)::

    {
        "debit_note_number": "DN/2026-27/00001",   # consecutive serial per entity+FY
        "financial_year": "2026-27",
        "issue_date": "2026-06-12",
        "entity_id": "...", "store_id": "...",
        "seller": {                                  # us (the issuer)
            "name": ..., "gstin": ..., "state_code": "20", "address": ...,
        },
        "vendor": {                                  # the recipient (debited)
            "vendor_id": ..., "name": ..., "gstin": ..., "state_code": "27", "address": ...,
        },
        "original_invoice": {"number": ..., "date": ...},   # purchase invoice ref
        "rtv_ref": {"type": "vendor_return"|"vendor_rma", "id": ...},
        "is_inter_state": True|False,                # decides IGST vs CGST+SGST
        "lines": [
            {
                "sku": ..., "description": ..., "hsn": ...,
                "qty": 2, "rate_paise": 150000,      # per-unit taxable rate in paise
                "taxable_paise": 300000,             # qty * rate (line taxable value)
                "gst_rate": 5.0,                     # percent
                "cgst_paise": ..., "sgst_paise": ..., "igst_paise": ...,
                "line_total_paise": ...,             # taxable + line tax
            },
            ...
        ],
        "totals": {
            "taxable_paise": ...,                    # sum of line taxable
            "cgst_paise": ..., "sgst_paise": ..., "igst_paise": ...,
            "tax_paise": ...,                        # cgst+sgst+igst
            "grand_total_paise": ...,                # taxable + tax
        },
    }

All money is INTEGER PAISE. GST split is paise-exact and mirrors the sales-side
tax splitter (CGST = SGST = floor(igst/2), CGST gets any +1 residual paisa).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_debit_note(
    rtv_doc: Dict[str, Any],
    vendor: Dict[str, Any],
    lines: List[Dict[str, Any]],
    serial: str,
    *,
    seller: Optional[Dict[str, Any]] = None,
    financial_year: Optional[str] = None,
    issue_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the GST-compliant debit-note document (pure -- no DB).

    Stub -- implemented incrementally after the survival commit.
    """
    raise NotImplementedError
