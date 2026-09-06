"""Tax invoice assembly, the GST split and the invoice / invoice.pdf doors.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from datetime import datetime
from fastapi import Depends, HTTPException
from typing import Optional
from ..auth import get_current_user
from ...dependencies import (
    get_customer_repository,
    get_order_repository,
    validate_store_access,
)
from ._shared import (
    item_to_frontend,
    router,
)


def _invoice_state_code(*candidates) -> str:
    """Best-effort 2-digit GST state code from the first usable candidate.
    Accepts a 2-digit code, a 2-letter / full state name, or a 15-char GSTIN
    (state = first two chars). ASCII-only; never raises.

    Thin alias over the shared resolver in org_validation, which the purchase
    side (a PO's vendor-vs-delivery-store place of supply), the purchase bill
    and the RTV debit note call too, so those four cannot answer two ways about
    the same row. NOT a claim about every state parser: the PRINTED invoice's
    HSN tax summary still decides IGST-vs-CGST/SGST with print_legal's own
    parser, which answers differently on some inputs (see the list in
    org_validation.resolve_state_code). Fail-soft to "" if org_validation
    cannot be imported, exactly as before.
    """
    try:
        from ...services.org_validation import resolve_state_code
    except Exception:  # noqa: BLE001
        return ""
    return resolve_state_code(*candidates)


def _customer_state_code(customer: Optional[dict]) -> str:
    """Resolve the customer's place-of-supply state code from (in order):
    customer GSTIN -> billing_address.state_code -> billing_address.state.
    Empty string when nothing usable is present."""
    if not isinstance(customer, dict):
        return ""
    addr = customer.get("billing_address") or {}
    if not isinstance(addr, dict):
        addr = {}
    return _invoice_state_code(
        customer.get("gstin"),
        addr.get("state_code"),
        addr.get("state"),
        customer.get("state_code"),
        customer.get("state"),
    )


def _build_invoice_gst_split(
    items: list, store: Optional[dict], customer: Optional[dict]
) -> dict:
    """C-6 (DELTA 4): per-rate CGST/SGST/IGST breakup for an order invoice.

    Place of supply = the CUSTOMER's state; supplier state = the STORE's state.
      * intra-state (or customer state unknown -> safe default for a single-
        state retailer): each rate's tax splits into CGST + SGST (each rate/2).
      * inter-state (both states known and different): the full tax is IGST.

    The split is computed from the order's ALREADY-STORED per-line
    `taxable_value` + `tax_amount`, so it reconciles to grand_total in BOTH
    pricing modes (inclusive / exclusive) without re-deriving tax. Never raises.

    Returns:
      {
        "place_of_supply": "<2-digit code or ''>",
        "place_of_supply_assumed": bool,   # True when defaulted to intra
        "interstate": bool,
        "store_gstin": "<gstin or ''>",
        "customer_gstin": "<gstin or ''>",
        "rows": [{"rate", "taxable", "cgst", "sgst", "igst", "tax"}],
        "totals": {"taxable", "cgst", "sgst", "igst", "tax"},
      }
    """
    store = store if isinstance(store, dict) else {}
    store_gstin = str(store.get("gstin") or "").strip()
    customer_gstin = (
        str((customer or {}).get("gstin") or "").strip()
        if isinstance(customer, dict)
        else ""
    )

    supplier_state = _invoice_state_code(store.get("state_code"), store_gstin)
    customer_state = _customer_state_code(customer)

    # Inter-state only when BOTH states are known and differ. Missing customer
    # state -> assume intra (CGST+SGST), the safe default for a single-state
    # retailer; flag it so the caller/print layer can note the assumption.
    if customer_state and supplier_state:
        interstate = customer_state != supplier_state
        assumed = False
    else:
        interstate = False
        assumed = True

    place_of_supply = customer_state or supplier_state

    # Aggregate stored per-line taxable + tax by GST rate.
    per_rate: dict = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        try:
            rate = round(float(it.get("gst_rate") or 0.0), 2)
        except (TypeError, ValueError):
            rate = 0.0
        try:
            taxable = float(it.get("taxable_value") or 0.0)
        except (TypeError, ValueError):
            taxable = 0.0
        try:
            tax = float(it.get("tax_amount") or 0.0)
        except (TypeError, ValueError):
            tax = 0.0
        agg = per_rate.setdefault(rate, {"taxable": 0.0, "tax": 0.0})
        agg["taxable"] = round(agg["taxable"] + taxable, 2)
        agg["tax"] = round(agg["tax"] + tax, 2)

    rows = []
    for rate in sorted(per_rate.keys()):
        taxable = round(per_rate[rate]["taxable"], 2)
        tax = round(per_rate[rate]["tax"], 2)
        # Shared with the purchase side (vendors.py PO GST) so an inter-state
        # supply can never split one way on a sale and another on a purchase.
        # The odd paisa lands on EITHER head (measured 50/50 over 100,000
        # odd-paise amounts -- see split_gst's docstring); the invariant is
        # cgst + sgst == the stored line tax exactly.
        from ...services.gst_rates import split_gst

        cgst, sgst, igst = split_gst(tax, interstate)
        rows.append(
            {
                "rate": rate,
                "taxable": taxable,
                "cgst": cgst,
                "sgst": sgst,
                "igst": igst,
                "tax": tax,
            }
        )

    totals = {
        "taxable": round(sum(r["taxable"] for r in rows), 2),
        "cgst": round(sum(r["cgst"] for r in rows), 2),
        "sgst": round(sum(r["sgst"] for r in rows), 2),
        "igst": round(sum(r["igst"] for r in rows), 2),
        "tax": round(sum(r["tax"] for r in rows), 2),
    }
    return {
        "place_of_supply": place_of_supply,
        "place_of_supply_assumed": assumed,
        "interstate": interstate,
        "store_gstin": store_gstin,
        "customer_gstin": customer_gstin,
        "rows": rows,
        "totals": totals,
    }


def _assemble_invoice(order_id: str, current_user: dict):
    """Shared invoice assembly for the JSON and PDF doors: IDOR/DRAFT/GSTIN
    gates, idempotent serial minting, C-6 CGST/SGST/IGST split. Returns
    (payload, order, customer_doc); (None, None, None) when no DB (the JSON
    door then serves its legacy mock envelope). ONE brain -- the PDF renderer
    (services/invoice_pdf.py) lays out, never recomputes."""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- an invoice carries customer
        # name/GSTIN + line-level pricing; only a caller with access to the
        # order's store may read it (SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if order.get("status") == "DRAFT":
            raise HTTPException(
                status_code=400, detail="Cannot generate invoice for DRAFT orders"
            )

        # GST compliance: store must have GSTIN configured before generating invoice
        store_id = order.get("store_id") or current_user.get("active_store_id")
        store_doc = None
        if store_id:
            try:
                from ...dependencies import get_store_repository

                store_repo = get_store_repository()
                if store_repo:
                    store_doc = store_repo.find_by_id(store_id)
                    if store_doc and not store_doc.get("gstin"):
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot generate invoice: store GSTIN is not configured. "
                            "Update store settings with a valid GSTIN first.",
                        )
            except HTTPException:
                raise
            except Exception:
                pass  # don't block invoice if store lookup fails

        # C-6 (DELTA 4): resolve the customer so the CGST/SGST/IGST split can
        # use the customer's state as the place of supply. Fail-soft: a missing
        # customer (e.g. walk-in) just leaves the customer state unknown, which
        # the split defaults to intra-state.
        customer_doc = None
        try:
            customer_id = order.get("customer_id")
            if customer_id:
                customer_repo = get_customer_repository()
                if customer_repo is not None:
                    customer_doc = customer_repo.find_by_id(customer_id)
        except Exception:
            customer_doc = None

        # Return the existing invoice or generate a new one.
        #
        # GST compliance (P3-A): a NEW invoice gets a consecutive serial that
        # is unique per (configured-prefix, financial year) -- e.g.
        # BV/2026-27/000123 -- allocated atomically via a counters doc so two
        # simultaneous bills can't share a serial (Rule 46(b)). The prefix is
        # the store's CONFIGURED invoice_prefix (falling back to global invoice
        # settings, then "INV"); store_doc was already loaded above for the
        # GSTIN check, so we hand it straight to the allocator. OLD orders keep
        # whatever invoice_number they already carry (including the legacy
        # BV/INV/{year}/{order_id[:8]} format); we never rewrite a stored
        # number, so historical invoices stay resolvable exactly as before.
        invoice_number = order.get("invoice_number")
        if not invoice_number:
            # Best-effort unique index (idempotent; no-op if already present).
            try:
                repo.ensure_invoice_index()
            except Exception:  # noqa: BLE001 - index is defense-in-depth only
                pass
            invoice_number = repo.next_invoice_number(store_id, store_doc=store_doc)
            repo.set_invoice(order_id, invoice_number)

        # Convert items to camelCase
        items_formatted = [item_to_frontend(item) for item in order.get("items", [])]

        # C-6 (DELTA 4): per-rate CGST/SGST/IGST tax summary + place of supply.
        gst_split = _build_invoice_gst_split(
            order.get("items", []), store_doc, customer_doc
        )

        payload = {
            "invoiceNumber": invoice_number,
            "orderId": order_id,
            "orderNumber": order.get("order_number"),
            "customerName": order.get("customer_name"),
            "grandTotal": order.get("grand_total"),
            "amountPaid": order.get("amount_paid"),
            "balanceDue": order.get("balance_due"),
            "items": items_formatted,
            "invoiceDate": order.get("invoice_date") or datetime.now().isoformat(),
            # C-6 (DELTA 4): GST place-of-supply split. All ADDITIVE -- existing
            # fields above are untouched.
            "placeOfSupply": gst_split["place_of_supply"],
            "placeOfSupplyAssumed": gst_split["place_of_supply_assumed"],
            "interstate": gst_split["interstate"],
            "storeGstin": gst_split["store_gstin"],
            "customerGstin": gst_split["customer_gstin"],
            "taxSummary": gst_split["rows"],
            "taxTotals": gst_split["totals"],
        }
        return payload, order, customer_doc

    return None, None, None


@router.get("/{order_id}/invoice")
async def get_invoice(order_id: str, current_user: dict = Depends(get_current_user)):
    """Get/generate invoice for order"""
    payload, _order, _customer = _assemble_invoice(order_id, current_user)
    if payload is None:
        return {"invoiceNumber": "BV/INV/2024/0001", "orderId": order_id}
    return payload


@router.get("/{order_id}/invoice.pdf")
async def get_invoice_pdf(
    order_id: str, current_user: dict = Depends(get_current_user)
):
    """A4 tax-invoice PDF (F52): the SAME assembly as the JSON door (same
    serial, same GST split -- minted idempotently, so JSON-then-PDF or
    PDF-then-JSON always show one invoice number), rendered server-side so
    it can be WhatsApp'd as a document and printed without the browser."""
    payload, order, customer_doc = _assemble_invoice(order_id, current_user)
    if payload is None or order is None:
        raise HTTPException(status_code=503, detail="Database not available")

    from fastapi.responses import Response

    from ...services.invoice_pdf import build_invoice_pdf

    pdf = build_invoice_pdf(payload, order, customer_doc)
    fname = str(payload.get("invoiceNumber") or order_id).replace("/", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{fname}.pdf"',
            "Cache-Control": "private, max-age=300",
        },
    )
