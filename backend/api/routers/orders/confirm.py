"""POST /orders/{order_id}/confirm.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from fastapi import Depends, HTTPException
from ..auth import get_current_user
from ...dependencies import get_order_repository, validate_store_access
from ._shared import (
    router,
    validate_status_transition,
)
from .workshop import _ensure_workshop_job_for_order
from .release import _claim_order_status


@router.post("/{order_id}/confirm")
async def confirm_order(order_id: str, current_user: dict = Depends(get_current_user)):
    """Confirm order (DRAFT -> CONFIRMED)"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if not validate_status_transition(order.get("status", ""), "CONFIRMED"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot confirm order — current status is {order.get('status')}",
            )

        if not order.get("items"):
            raise HTTPException(
                status_code=400, detail="Cannot confirm order with no items"
            )

        # Guarded claim, not a blind stamp: a cancel that wins its own claim
        # between this handler's read and this write must NOT be overwritten.
        if _claim_order_status(
            repo, order_id, "CONFIRMED", ("DRAFT",), current_user.get("user_id")
        ):
            # POS operational-wins: guarantee a fitting order has a workshop/lab
            # job once it's committed. Idempotent + fail-soft (the POS client may
            # already have created it; a non-POS confirm path may not have).
            workshop_job_id = _ensure_workshop_job_for_order(
                order, current_user.get("user_id")
            )
            return {
                "order_id": order_id,
                "status": "CONFIRMED",
                "message": "Order confirmed",
                "workshop_job_id": workshop_job_id,
            }

        raise HTTPException(status_code=500, detail="Failed to confirm order")

    return {"order_id": order_id, "status": "CONFIRMED"}
