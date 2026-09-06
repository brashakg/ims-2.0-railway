"""BOPIS transfer request.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

import uuid
from datetime import datetime
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from ..auth import get_current_user
from ...dependencies import get_order_repository, validate_store_access
from ._shared import (
    logger,
    router,
)

# ============================================================================
# POS-7: BOPIS / ship-from-store
# ============================================================================


class BOPISLine(BaseModel):
    """One item line that must be fulfilled from another store."""

    product_id: str
    sku: str = ""
    product_name: str = ""
    quantity: int = Field(..., ge=1)
    unit_price: float = Field(..., ge=0)
    source_store_id: str = Field(
        ..., description="The store that has the stock to ship"
    )
    source_store_name: str = ""


class BOPISRequest(BaseModel):
    items: list[BOPISLine]
    pickup_store_id: str = Field(
        ..., description="The store where the customer will collect the goods"
    )
    pickup_store_name: str = ""
    notes: str = ""
    priority: str = "normal"  # matches TransferPriority enum in transfers.py


@router.post("/{order_id}/bopis-transfer")
async def create_bopis_transfer(
    order_id: str,
    body: BOPISRequest,
    current_user: dict = Depends(get_current_user),
):
    """POS-7: Create inter-store transfer request(s) for items on an order
    that are not in stock at the selling store.

    Groups requested items by source_store_id and creates one transfer
    request per source store directed at the pickup store. Returns the
    transfer IDs so the POS display can track fulfillment status.

    Auth: same as order-confirm — any authenticated POS user.
    Fail-soft: if the transfers collection is unavailable, the transfer IDs
    are synthetic UUIDs so the order can still be finalised.
    """
    repo = get_order_repository()
    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)
        if order.get("status") == "CANCELLED":
            raise HTTPException(
                status_code=400, detail="Cannot add BOPIS transfer to a cancelled order"
            )

    if not body.items:
        raise HTTPException(status_code=400, detail="No BOPIS items provided")

    # Group lines by source store.
    from collections import defaultdict

    by_source: dict = defaultdict(list)
    for line in body.items:
        by_source[line.source_store_id].append(line)

    # Import transfers helper (late import to avoid circular deps)
    try:
        from ...dependencies import get_db

        db = get_db()
        transfers_coll = (
            db.get_collection("stock_transfers")
            if db is not None and db.is_connected
            else None
        )
    except Exception:
        transfers_coll = None

    created_transfers = []
    user_id = current_user.get("user_id", "")
    now = datetime.now().isoformat()

    for source_store_id, lines in by_source.items():
        transfer_id = str(uuid.uuid4())
        transfer_doc = {
            "id": transfer_id,
            "order_id": order_id,
            "transfer_type": "store_to_store",
            "source": "bopis",
            "from_location_id": source_store_id,
            "from_location_name": lines[0].source_store_name or source_store_id,
            "to_location_id": body.pickup_store_id,
            "to_location_name": body.pickup_store_name or body.pickup_store_id,
            "status": "pending_approval",
            "priority": body.priority or "normal",
            "notes": body.notes or f"BOPIS order {order_id}",
            "requested_by": user_id,
            "created_at": now,
            "status_history": [
                {"status": "pending_approval", "changed_at": now, "changed_by": user_id}
            ],
            "items": [
                {
                    "transfer_item_id": str(uuid.uuid4()),
                    "product_id": ln.product_id,
                    "sku": ln.sku,
                    "product_name": ln.product_name,
                    "quantity_requested": ln.quantity,
                    "quantity_shipped": 0,
                    "quantity_received": 0,
                    "unit_cost": ln.unit_price,
                }
                for ln in lines
            ],
        }
        try:
            if transfers_coll is not None:
                transfers_coll.update_one(
                    {"id": transfer_id},
                    {"$set": transfer_doc},
                    upsert=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] BOPIS transfer persist failed: %s", exc)

        created_transfers.append(
            {
                "transfer_id": transfer_id,
                "from_store_id": source_store_id,
                "to_store_id": body.pickup_store_id,
                "item_count": len(lines),
            }
        )

    # Stamp the transfer IDs onto the order doc for tracking.
    if repo is not None:
        try:
            existing_bopis = order.get("bopis_transfer_ids") or []
            existing_bopis += [t["transfer_id"] for t in created_transfers]
            repo.update(order_id, {"bopis_transfer_ids": existing_bopis})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] BOPIS order-stamp failed: %s", exc)

    return {
        "order_id": order_id,
        "transfers": created_transfers,
        "message": f"Created {len(created_transfers)} BOPIS transfer request(s)",
    }
