"""GET /orders/{order_id} and the plain PUT update.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from fastapi import Depends, HTTPException
from ..auth import get_current_user
from ...dependencies import get_order_repository, validate_store_access
from ._shared import (
    _stamp_status_actor_names,
    order_to_frontend,
    router,
)
from .models import OrderUpdate


@router.get("/{order_id}")
async def get_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Get order details"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is not None:
            # Store-scope: only view an order in a store the user can access
            # (raises 403 otherwise; SUPERADMIN/ADMIN pass through).
            validate_store_access(order.get("store_id"), current_user)
            # Backfill-safe: ensure a public tracking token exists so the
            # staff-facing order view can render the customer-tracking QR
            # even for orders created before that field existed. Fail-soft.
            if not order.get("tracking_token"):
                try:
                    from ..portal import ensure_tracking_token

                    order["tracking_token"] = ensure_tracking_token(repo, order)
                except Exception:  # noqa: BLE001
                    pass
            _stamp_status_actor_names([order])
            return order_to_frontend(order)
        raise HTTPException(status_code=404, detail="Order not found")

    return {"id": order_id}


@router.put("/{order_id}")
async def update_order(
    order_id: str, order: OrderUpdate, current_user: dict = Depends(get_current_user)
):
    """Update order (only DRAFT orders)"""
    repo = get_order_repository()

    if repo is not None:
        existing = repo.find_by_id(order_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(existing.get("store_id"), current_user)

        if existing.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Only DRAFT orders can be updated"
            )

        update_data = order.model_dump(exclude_unset=True)
        if "expected_delivery" in update_data and update_data["expected_delivery"]:
            update_data["expected_delivery"] = update_data[
                "expected_delivery"
            ].isoformat()

        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(order_id, update_data):
            # Audit alert (May 2026) — fire-and-forget, never blocks order edit
            try:
                from ...services.audit_alerts import alert_order_edited

                fresh = repo.find_by_id(order_id) or {}
                import asyncio as _aio

                _aio.create_task(
                    alert_order_edited(
                        order_id,
                        before=existing,
                        after=fresh,
                        user_id=current_user.get("user_id"),
                    )
                )
            except Exception:
                pass
            return {"order_id": order_id, "message": "Order updated"}

        raise HTTPException(status_code=500, detail="Failed to update order")

    return {"order_id": order_id, "message": "Order updated"}
