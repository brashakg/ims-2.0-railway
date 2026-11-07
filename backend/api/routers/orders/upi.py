"""UPI QR for an order balance.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from fastapi import Depends, HTTPException
from ..auth import get_current_user
from ...dependencies import get_order_repository, validate_store_access
from ._shared import refuse_if_advance_refunded, router

# ============================================================================
# POS-6: UPI QR code endpoint
# ============================================================================


@router.get("/{order_id}/upi-qr")
async def get_upi_qr(
    order_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return a UPI deep-link (and optional QR data-URI) for an order.

    Resolves the store's UPI VPA from the stores collection (upi_vpa field).
    Returns 400 when the store has no VPA configured so the operator knows
    exactly which setting to fill in.

    No Razorpay creds required -- the UPI link is pure NPCI standard.
    The QR data-URI (base-64 PNG) is included when the optional `qrcode`
    Python package is installed; otherwise only the link is returned.
    Fail-soft: any DB error returns a useful error response (never 500).
    """
    repo = get_order_repository()

    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Store-scope check (same pattern as GET /orders/{id}).
    validate_store_access(order.get("store_id"), current_user)

    # The QR asks for what is still OWED - the same reading the payment door
    # uses (delivery_gate.order_balance_due) - never the whole bill: a part
    # payment used to leave a QR for grand_total (audit L2-PAYMENT-F03). A
    # cancelled / advance-refunded / fully-paid order has nothing to collect.
    if order.get("status") == "CANCELLED":
        raise HTTPException(status_code=400, detail="This order is cancelled - there is nothing to collect.")
    refuse_if_advance_refunded(order, "take a payment")
    from ...services.delivery_gate import order_balance_due

    balance_due = round(order_balance_due(order) or 0.0, 2)
    if balance_due <= 0:
        raise HTTPException(status_code=400, detail="Nothing is due on this order - no UPI QR to show.")

    store_id = order.get("store_id") or current_user.get("active_store_id") or ""

    # Resolve the store's UPI VPA.
    try:
        from ...dependencies import get_db as _get_db_dep

        db_conn = _get_db_dep()
        raw_db = getattr(db_conn, "db", None) if db_conn is not None else None
    except Exception:
        raw_db = None

    from ...services.upi_qr import (
        _resolve_store_vpa,
        _resolve_merchant_name,
        build_upi_link,
        build_qr_data_uri,
    )

    vpa = _resolve_store_vpa(raw_db, store_id)
    if not vpa:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Store '{store_id}' does not have a UPI VPA configured. "
                "Add the shop's UPI VPA in Organization (left menu): the "
                "store's Virtual Payment Address (e.g. bettervision.bok@upi)."
            ),
        )

    merchant = _resolve_merchant_name(raw_db, store_id)
    order_ref = order.get("order_number") or order_id

    upi_link = build_upi_link(vpa, merchant, balance_due, order_ref)
    qr_image = build_qr_data_uri(upi_link)

    return {
        "order_id": order_id,
        "order_number": order_ref,
        "store_id": store_id,
        "vpa": vpa,
        "merchant": merchant,
        "amount": balance_due,
        "currency": "INR",
        "upi_link": upi_link,
        "qr_image": qr_image,  # None when qrcode lib absent
        "qr_available": qr_image is not None,
    }
