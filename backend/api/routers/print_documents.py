"""
IMS 2.0 - Print Documents Router (Delivery Challan)
===================================================

Server-side HTML render endpoints for the Rule 55 Delivery Challan, which had
a frontend modal component (DeliveryChallanPrint.tsx) but NO backend route to
feed it. The challan documents goods that move WITHOUT a tax invoice:
  * for a sales ORDER (goods handed to / delivered to the customer), and
  * for an inter-store TRANSFER (stock_transfers).

The HTML is produced by api.services.print_render.render_delivery_challan,
which reuses the existing api.services.print_legal statutory primitives
(LegalHeader / Rule-55 copy markers / statutory_footer). Returns
media_type="text/html" so the browser prints it directly.

Routes (mounted at /api/v1/print):
  GET /api/v1/print/delivery-challan/order/{order_id}
  GET /api/v1/print/delivery-challan/transfer/{transfer_id}
  GET /api/v1/print/transfer-invoice/{transfer_id}  (owner / CA 2026-10-18: a
      move between two GST registrations travels on a TAX INVOICE + e-way bill
      data instead of a challan)

Auth: POS-capable roles + ACCOUNTANT (the challan render is read-only but
surfaces party + line data, so it sits one tier wider than POS writes); a
VALUED transfer challan carries cost, so managers and accounts only
(may_print_challan).
Store-scope is enforced (validate_store_access) so a store-bound user cannot
print a challan for another store's order/transfer. SUPERADMIN/ADMIN pass.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_customer_repository,
    validate_store_access,
)
from ..services.cost_mask import can_see_cost
from ..services.print_render import (
    render_delivery_challan,
    render_transfer_tax_invoice,
)
from ..services.print_identity import (
    assert_issuing_identity,
    load_entity_for_store,
    load_overrides,
    load_store,
)

router = APIRouter()

# POS-capable roles + ACCOUNTANT may render a delivery challan. Mirrors the
# orders POS_WRITE_ROLES set plus ACCOUNTANT (back-office staff who reconcile
# dispatches). Read-only document, but it surfaces party + line data, so it is
# gated above the bare-authenticated tier.
_CHALLAN_ROLES = (
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "SALES_CASHIER",
    "SALES_STAFF",
    "ACCOUNTANT",
)


def may_print_challan(current_user: dict, valued: bool = False) -> bool:
    """THE challan print rule by role: the challan roles print a delivery
    challan; a VALUED one (a move between two GST registrations, D13) carries
    the units' cost, so only whom the one cost rule admits -- the owner and
    the accounts roles (owner ruling 2026-10-12, which narrowed 2026-10-08's
    managers and accounts)."""
    roles = (current_user or {}).get("roles") or []
    return any(r in _CHALLAN_ROLES for r in roles) and (
        not valued or can_see_cost(current_user, "product")
    )


def _require_challan_role(current_user: dict) -> None:
    if not may_print_challan(current_user):
        raise HTTPException(
            status_code=403,
            detail="Not permitted to print a delivery challan.",
        )


def challan_gate(transfer: Dict[str, Any], current_user: dict) -> tuple:
    """THE rule for whether a transfer's challan prints for this caller, before
    its lines are read: the challan roles; a move IMS cannot place prints for
    no one (ship's own 400); a VALUED one only for managers and accounts and
    only once shipped (its value is what left the shop). Raises the route's
    refusal, else returns the registrations (transfers._transfer_registrations).
    The route refuses by it and every transfer reply hands the screen its
    answer (transfers._can_print_challan), so the button never offers a
    refusal."""
    from .transfers import _get_db, _gstin_gap, _transfer_registrations

    _require_challan_role(current_user)
    src, dst, valued = _transfer_registrations(_get_db(), transfer)
    if valued is None:
        # IMS cannot place a shop on a registration: no paper either way.
        raise HTTPException(status_code=400, detail=_gstin_gap(transfer, src, dst))
    if valued:
        # D7: the value is the units' own cost -- never a counter role's.
        if not may_print_challan(current_user, valued=True):
            raise HTTPException(
                status_code=403,
                detail="This challan carries the stock's cost. "
                "Ask accounts to print it.",
            )
        if not transfer.get("stock_shipped"):
            raise HTTPException(
                status_code=409,
                detail="Ship the transfer first: a challan between two GST "
                "registrations is valued at the units that leave the shop.",
            )
    return src, dst, valued


def _challan_number(prefix: str, ref: str) -> str:
    """Best-effort challan number derived from the source doc reference. The
    challan is not a statutory serial (Rule 55 only requires a unique number),
    so deriving it from the order/transfer ref keeps it stable + reprintable."""
    ref = str(ref or "").strip()
    short = ref[-8:] if ref else uuid.uuid4().hex[:8].upper()
    return "DC/{0}/{1}".format(prefix, short)


@router.get("/delivery-challan/order/{order_id}", response_class=HTMLResponse)
async def delivery_challan_for_order(
    order_id: str,
    copy: str = Query("ORIGINAL", description="ORIGINAL | DUPLICATE | TRIPLICATE"),
    auto_print: bool = Query(False, description="Auto-trigger the print dialog on load"),
    current_user: dict = Depends(get_current_user),
) -> HTMLResponse:
    """Render a delivery challan for a sales order (goods moving to the customer)."""
    _require_challan_role(current_user)

    repo = get_order_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Order store unavailable")
    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Store-scope guard (mirrors GET /orders/{id}).
    validate_store_access(order.get("store_id"), current_user)

    store = load_store(order.get("store_id"))
    entity = load_entity_for_store(store)
    # Fail loudly rather than printing an identity-less challan: the seller
    # GSTIN is the order's own shop's (store.gstin, the one its tax invoice
    # issues from), and a routed online order is refused on THE seller check
    # its booking held it on -- never printed under another state's GSTIN.
    assert_issuing_identity(store, require_gstin=True, entity=entity)
    from ..services.online_fulfillment_route import seller_problem

    bad = seller_problem(order, store, load_store)
    if bad:
        raise HTTPException(
            status_code=400, detail=f"Cannot print a delivery challan: {bad['message']}"
        )
    overrides = load_overrides(entity, "delivery_challan") or load_overrides(
        entity, "tax_invoice"
    )

    # Resolve customer for the consignee block (fail-soft for walk-ins).
    customer: Dict[str, Any] = {}
    try:
        cid = order.get("customer_id")
        if cid:
            crepo = get_customer_repository()
            if crepo is not None:
                customer = crepo.find_by_id(cid) or {}
    except Exception:  # noqa: BLE001
        customer = {}

    consignee_name = (
        order.get("customer_name")
        or customer.get("name")
        or "Walk-in Customer"
    )
    addr = customer.get("billing_address") or customer.get("address") or {}
    from ..services.invoice_pdf import ship_to_lines

    ship_to = ship_to_lines(order)
    if ship_to:
        # The goods travel to the order's own delivery address (an online
        # order's checkout address), not the customer record's.
        consignee_address = ", ".join(ship_to)
    elif isinstance(addr, dict):
        consignee_address = ", ".join(
            str(p)
            for p in [
                addr.get("line1") or addr.get("street") or addr.get("address"),
                addr.get("city"),
                addr.get("state"),
                addr.get("pincode"),
            ]
            if p
        )
    else:
        consignee_address = str(addr or "")

    items: List[Dict[str, Any]] = []
    for it in order.get("items", []) or []:
        # Shipping is billed on the invoice; it is not goods in the parcel.
        if not isinstance(it, dict) or it.get("item_type") == "SHIPPING":
            continue
        items.append(
            {
                "product_name": it.get("product_name") or it.get("name") or "",
                "hsn_code": it.get("hsn_code") or it.get("hsn") or "",
                "qty": it.get("quantity") or it.get("qty") or 1,
                "serial": it.get("serial_number") or it.get("serial") or "",
            }
        )

    html = render_delivery_challan(
        entity=entity,
        store=store,
        challan_number=_challan_number("ORD", order.get("order_number") or order_id),
        challan_date=order.get("invoice_date")
        or order.get("created_at")
        or datetime.now(timezone.utc),
        consignee_name=consignee_name,
        consignee_address=consignee_address,
        to_label=consignee_name,
        items=items,
        notes="Against Order " + str(order.get("order_number") or order_id),
        copy_marker=copy,
        transport_reason="Outward delivery to customer",
        overrides=overrides,
        auto_print=bool(auto_print),
    )
    return HTMLResponse(content=html)


@router.get("/delivery-challan/transfer/{transfer_id}", response_class=HTMLResponse)
async def delivery_challan_for_transfer(
    transfer_id: str,
    copy: str = Query("ORIGINAL", description="ORIGINAL | DUPLICATE | TRIPLICATE"),
    auto_print: bool = Query(False, description="Auto-trigger the print dialog on load"),
    current_user: dict = Depends(get_current_user),
) -> HTMLResponse:
    """Render the paper an inter-store stock transfer travels on.

    A move inside one GST registration: the unvalued delivery challan. A move
    between two registrations (transfers._transfer_registrations, the rule
    the FIN-3 mirror bill books on): the owner / CA ruled it a TAX INVOICE
    (2026-10-18, replacing D13's valued challan), so the `valued` branch
    prints the transfer tax invoice + e-way bill data, numbered at ship from
    the sending shop's T series -- the units' own cost (stamped at ship),
    HSN, both GSTINs. A crossing move shipped before ship numbered it keeps
    D13's valued challan (its paper travelled). Every paper carries the
    transfer's own number, what shipped and the units' barcodes. Printing
    writes nothing: the books are the mirror bill's (GSTR-1 B2B)."""
    _require_challan_role(current_user)

    # Reuse the transfers router persistence + access guard + the one rule.
    from .transfers import (
        _assert_transfer_access,
        _first_cost,
        _get_transfer,
        _line_expected_qty,
        _line_hsn,
        _require_hsn,
        _shipped_line_value,
    )

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    _assert_transfer_access(transfer, current_user, side="either")

    from_id = transfer.get("from_location_id")
    store = load_store(from_id)
    entity = load_entity_for_store(store)
    # Fail loudly rather than printing an identity-less challan.
    assert_issuing_identity(store, entity=entity)
    overrides = load_overrides(entity, "delivery_challan") or load_overrides(
        entity, "tax_invoice"
    )
    to_store = load_store(transfer.get("to_location_id"))
    to_entity = load_entity_for_store(to_store)
    from_name = transfer.get("from_location_name") or ""
    to_name = transfer.get("to_location_name") or ""

    src, dst, valued = challan_gate(transfer, current_user)
    from_gstin, to_gstin = src[1], dst[1]
    shipped = bool(transfer.get("stock_shipped"))

    items: List[Dict[str, Any]] = []
    for it in transfer.get("items", []) or []:
        if not isinstance(it, dict):
            continue
        row = {
            "product_name": it.get("product_name") or it.get("sku") or "",
            "hsn_code": _line_hsn(it),
            # What left the shop once shipped; the request before that.
            "qty": _line_expected_qty(it)
            if shipped
            else (it.get("quantity_requested") or it.get("quantity") or it.get("qty") or 1),
            "serial": ", ".join(it.get("shipped_barcodes") or [])
            or it.get("serial_number")
            or it.get("notes")
            or "",
        }
        if valued:
            row["rate"] = _first_cost(it.get("unit_cost"))
            row["value"] = _shipped_line_value(it)
            if row["qty"] and row["value"] <= 0:
                # Shipped before the value was stamped (or with no cost on
                # file): never print it at Rs 0 or short.
                raise HTTPException(
                    status_code=409,
                    detail=f"No value was recorded for {row['product_name']} when "
                    "this transfer shipped, so its valued challan cannot be printed.",
                )
            if row["qty"]:
                _require_hsn(row["product_name"], row["hsn_code"])
        items.append(row)
    if valued and not any(r["qty"] for r in items):
        # F51: never a Rs 0 paper between two registrations.
        raise HTTPException(
            status_code=409,
            detail="Nothing left the shop on this transfer, so there is no valued "
            "challan to print.",
        )

    to_legal = str((to_entity or {}).get("legal_name") or "").strip()
    consignee_address = _address(to_store)
    to_label = f"{to_legal} ({to_name})" if to_legal and to_name else (to_legal or to_name)

    if valued and transfer.get("tax_invoice_number"):
        from .transfers import _get_db, _mirror_bill_lines
        from ..services.purchase_invoice_engine import classify_supply

        interstate = bool(classify_supply(from_gstin, to_gstin)["interstate"])
        html = render_transfer_tax_invoice(
            entity=entity,
            store=store,
            invoice_number=transfer["tax_invoice_number"],
            invoice_date=transfer.get("tax_invoice_date") or transfer.get("shipped_at"),
            supplier_name=str((entity or {}).get("legal_name") or "").strip() or from_name,
            supplier_address=_address(store),
            supplier_gstin=from_gstin,
            recipient_name=to_label,
            recipient_address=consignee_address,
            recipient_gstin=to_gstin,
            # The invoice as issued (frozen at ship); a move numbered before
            # the freeze is priced afresh.
            lines=transfer.get("tax_invoice_lines")
            or _mirror_bill_lines(_get_db(), transfer, interstate, shipped=True),
            transfer_number=transfer.get("transfer_number") or "",
            copy_marker=copy,
            overrides=load_overrides(entity, "tax_invoice"),
            auto_print=bool(auto_print),
        )
        return HTMLResponse(content=html)

    html = render_delivery_challan(
        entity=entity,
        store=store,
        challan_number=transfer.get("transfer_number")
        or _challan_number("TRF", transfer.get("id") or transfer_id),
        challan_date=transfer.get("shipped_at")
        or transfer.get("created_at")
        or datetime.now(timezone.utc),
        from_label=from_name,
        to_label=to_label,
        consignee_name=to_name,
        consignee_address=consignee_address,
        consignor_gstin=from_gstin,
        consignee_gstin=to_gstin,
        valued=valued,
        items=items,
        notes=transfer.get("notes") or "",
        copy_marker=copy,
        transport_reason="Stock transfer between GST registrations, valued at cost "
        "(not a sale)"
        if valued
        else "Inter-store stock transfer",
        overrides=overrides,
        auto_print=bool(auto_print),
    )
    return HTMLResponse(content=html)


def _address(store: Dict[str, Any]) -> str:
    return ", ".join(
        str(p)
        for p in (
            (store or {}).get("address"),
            (store or {}).get("city"),
            (store or {}).get("state"),
            (store or {}).get("pincode"),
        )
        if p
    )


@router.get("/transfer-invoice/{transfer_id}", response_class=HTMLResponse)
async def tax_invoice_for_transfer(
    transfer_id: str,
    copy: str = Query("ORIGINAL", description="ORIGINAL | DUPLICATE | TRIPLICATE"),
    auto_print: bool = Query(False, description="Auto-trigger the print dialog on load"),
    current_user: dict = Depends(get_current_user),
) -> HTMLResponse:
    """The tax invoice (and e-way bill data) of a stock move between two GST
    registrations -- owner / CA 2026-10-18; numbered at ship from the sending
    shop's T series (transfers._transfer_tax_invoice). The one transfer paper
    route prints it (its valued branch: who prints it, the lines, the
    refusals); this door only refuses a transfer that has no tax invoice.
    Either side of the move prints it, so the receiving shop keeps its
    inward copy."""
    from .transfers import _assert_transfer_access, _get_transfer

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    _assert_transfer_access(transfer, current_user, side="either")
    if not transfer.get("tax_invoice_number"):
        raise HTTPException(
            status_code=409,
            detail="This transfer has no tax invoice: a move inside one GST "
            "registration travels on a delivery challan, and one shipped before "
            "transfer tax invoices began on its valued challan.",
        )
    return await delivery_challan_for_transfer(transfer_id, copy, auto_print, current_user)
