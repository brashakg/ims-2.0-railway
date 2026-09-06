"""Per-line prescription validation for the order create / add-item doors.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from fastapi import HTTPException
from ...services.rx_validation import (
    _validate_axis as _validate_rx_axis,
    _validate_rx_number as _validate_rx_power,
    is_rx_required_line as _is_rx_required_line,
)

# ============================================================================
# Rx validation for order lines (BUG-005 patient-safety + BUG-006 Rx-required)
# ============================================================================
# Roles permitted to override an EXPIRED prescription (Store-Manager and up).
# A SALES_STAFF / SALES_CASHIER must escalate; HQ roles + the store manager may
# proceed on an expired Rx (a deliberate, audited clinical decision).
_RX_EXPIRY_OVERRIDE_ROLES = (
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
)


def _validate_order_line_rx(
    item, customer_id: str, current_user: dict, product_doc=None
) -> None:
    """Validate ONE order line's clinical Rx data. Raises HTTPException(422) on a
    bad/missing value -- never 500s. Pure validation; touches no pricing/GST math.

    BUG-005: range / 0.25-step / whole-axis / cyl-requires-axis check on the
             line's sph/cyl/add/axis via the SAME canonical clinical validators.
    BUG-006: a SPECTACLE (Rx) lens line must carry a prescription_id that
             resolves to a real prescription for THIS customer; an expired Rx is
             allowed only for Store-Manager+. CONTACT LENSES are EXEMPT from this
             hard gate (owner policy 2026-06-18 "block Rx lenses, allow contacts").
    Frame-only / contact-lens / non-Rx lines are not Rx-gated -- but their
    powers (if any) are STILL range-checked above (BUG-005 is universal).

    SECURITY (Rx-item_type spoof): the Rx-required decision MUST key off the
    resolved PRODUCT MASTER's canonical item_type / category (`product_doc`),
    not the client-supplied item.item_type / item.category -- otherwise a
    spectacle-lens product sent with item_type='FRAME' would skip the hard Rx
    requirement and reach the lab without a prescription. The client value is
    used ONLY as a fallback when the resolved product doc lacks item_type /
    category (virtual lens-*/custom-* lines, or a not-found product).
    """
    name = (
        getattr(item, "product_name", None) or getattr(item, "product_id", "") or "item"
    )

    # --- BUG-005: power-value validation (range + 0.25 grid + axis rules) ------
    sph = getattr(item, "sph", None)
    cyl = getattr(item, "cyl", None)
    add = getattr(item, "add", None)
    axis = getattr(item, "axis", None)
    try:
        _validate_rx_power(sph, "sph")
        _validate_rx_power(cyl, "cyl")
        _validate_rx_power(add, "add")
        # AXIS: whole 1-180, and MANDATORY when cyl is non-zero.
        _validate_rx_axis(axis, cyl=cyl)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid prescription power on '{name}': {exc}",
        )

    # --- BUG-006: Rx-required for SPECTACLE-lens lines (contacts EXEMPT) -------
    # SECURITY: prefer the RESOLVED product master's canonical item_type /
    # category over the client-supplied values so a lens line cannot skip the Rx
    # requirement by claiming item_type='FRAME'. Fall back to the client value
    # only per-field when the product doc has nothing (virtual/not-found lines).
    client_item_type = getattr(item, "item_type", None)
    client_category = getattr(item, "category", None)
    item_type = client_item_type
    category = client_category
    if product_doc:
        prod_item_type = product_doc.get("item_type")
        prod_category = product_doc.get("category")
        if prod_item_type:
            item_type = prod_item_type
        if prod_category:
            category = prod_category
    if not _is_rx_required_line(item_type, category):
        return  # frame / sunglass / accessory / service / CONTACT-LENS -> no Rx needed

    rx_id = (getattr(item, "prescription_id", None) or "").strip()
    if not rx_id:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{name}' is a prescription lens and requires a linked "
                f"prescription. Select the customer's Rx before billing."
            ),
        )

    # Resolve the Rx and confirm it belongs to THIS customer. Fail-SOFT on a
    # missing/unavailable prescription repo (do not 500 / do not block billing
    # when the clinical store is down) -- the prescription_id presence check
    # above already enforces the core BUG-006 requirement.
    try:
        from ...dependencies import get_prescription_repository

        rx_repo = get_prescription_repository()
    except Exception:  # noqa: BLE001
        rx_repo = None
    if rx_repo is None:
        return

    try:
        rx = rx_repo.find_by_id(rx_id)
    except Exception:  # noqa: BLE001
        return  # repo error -> fail-soft, don't block the sale

    if rx is None:
        raise HTTPException(
            status_code=422,
            detail=f"Prescription '{rx_id}' for '{name}' was not found.",
        )
    # The Rx must belong to the order's customer (no cross-customer Rx).
    rx_customer = rx.get("customer_id") or rx.get("customerId")
    if customer_id and rx_customer and str(rx_customer) != str(customer_id):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Prescription '{rx_id}' does not belong to this customer; "
                f"select a prescription for the billed customer."
            ),
        )

    # Expiry: an expired Rx may only be dispensed by Store-Manager+ (override).
    try:
        from ..prescriptions import _rx_validity

        _expiry, is_valid = _rx_validity(rx)
    except Exception:  # noqa: BLE001
        is_valid = None  # can't compute -> don't block (fail-soft)
    if is_valid is False:
        roles = current_user.get("roles", []) or []
        if not any(r in roles for r in _RX_EXPIRY_OVERRIDE_ROLES):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Prescription '{rx_id}' for '{name}' has EXPIRED. A Store "
                    f"Manager or higher must approve dispensing on an expired Rx."
                ),
            )
