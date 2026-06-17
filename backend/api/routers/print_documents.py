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

Auth: POS-capable roles + ACCOUNTANT (the challan render is read-only but
surfaces party + line data, so it sits one tier wider than POS writes).
Store-scope is enforced (validate_store_access) so a store-bound user cannot
print a challan for another store's order/transfer. SUPERADMIN/ADMIN pass.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from .auth import get_current_user
from ..dependencies import (
    get_order_repository,
    get_customer_repository,
    validate_store_access,
)
from ..services.print_render import render_delivery_challan

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


def _require_challan_role(current_user: dict) -> None:
    roles = current_user.get("roles", []) if current_user else []
    if not any(r in _CHALLAN_ROLES for r in roles):
        raise HTTPException(
            status_code=403,
            detail="Not permitted to print a delivery challan.",
        )


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _load_store(store_id: Optional[str]) -> Dict[str, Any]:
    """Load a store doc (by store_id) -- empty dict when unavailable."""
    if not store_id:
        return {}
    db = _get_db()
    if db is None:
        return {}
    try:
        return db.get_collection("stores").find_one({"store_id": store_id}) or {}
    except Exception:  # noqa: BLE001
        return {}


def _load_entity_for_store(store: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the legal entity for a store via its entity_id -- empty when none."""
    eid = (store or {}).get("entity_id")
    if not eid:
        return {}
    db = _get_db()
    if db is None:
        return {}
    try:
        return db.get_collection("entities").find_one({"entity_id": eid}) or {}
    except Exception:  # noqa: BLE001
        return {}


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

    store = _load_store(order.get("store_id"))
    entity = _load_entity_for_store(store)

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
    if isinstance(addr, dict):
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
        if not isinstance(it, dict):
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
    """Render a delivery challan for an inter-store stock transfer."""
    _require_challan_role(current_user)

    # Reuse the transfers router persistence + access guard.
    from .transfers import _get_transfer, _assert_transfer_access

    transfer = _get_transfer(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")
    _assert_transfer_access(transfer, current_user, side="either")

    from_id = transfer.get("from_location_id")
    store = _load_store(from_id)
    entity = _load_entity_for_store(store)

    items: List[Dict[str, Any]] = []
    for it in transfer.get("items", []) or []:
        if not isinstance(it, dict):
            continue
        items.append(
            {
                "product_name": it.get("product_name") or it.get("sku") or "",
                "hsn_code": it.get("hsn_code") or it.get("hsn") or "",
                "qty": it.get("quantity_requested")
                or it.get("quantity")
                or it.get("qty")
                or 1,
                "serial": it.get("serial_number") or it.get("notes") or "",
            }
        )

    html = render_delivery_challan(
        entity=entity,
        store=store,
        challan_number=_challan_number("TRF", transfer.get("id") or transfer_id),
        challan_date=transfer.get("created_at") or datetime.now(timezone.utc),
        from_label=transfer.get("from_location_name") or "",
        to_label=transfer.get("to_location_name") or "",
        consignee_name=transfer.get("to_location_name") or "",
        items=items,
        notes=transfer.get("notes") or "",
        copy_marker=copy,
        transport_reason="Inter-store stock transfer",
        auto_print=bool(auto_print),
    )
    return HTMLResponse(content=html)
