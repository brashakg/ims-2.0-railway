"""E-invoice (IRN) trigger for a single order.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from fastapi import Depends, HTTPException
from ..auth import get_current_user
from ...dependencies import can_access_store_scoped, validate_store_access
from ._shared import _get_db, router

# ============================================================================
# GST e-invoice (IRN + signed QR) -- FIN-1
# ============================================================================
# DARK by default: returns {status: "SIMULATED"} until IMS_EINVOICE_ENABLED=1
# AND GSP credentials are present in the integrations collection. Owner-gated.
# Roles mirror the sibling finance routes: ACCOUNTANT, ADMIN, SUPERADMIN.

_EINVOICE_ROLES = ("ACCOUNTANT", "ADMIN", "SUPERADMIN")


@router.post("/einvoice/{order_id}")
async def trigger_einvoice(
    order_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Trigger IRN generation for a single order.

    Returns the einvoice result dict (status SIMULATED | GENERATED | SKIPPED |
    FAILED). DARK by default -- caller always gets a structured response; never
    a 500. Finance roles only: ACCOUNTANT / ADMIN / SUPERADMIN.
    """
    from api.routers.auth import require_roles
    from api.services.einvoice import generate_irn

    role = str(
        current_user.get("activeRole") or (current_user.get("roles") or [""])[0] or ""
    )
    if role not in _EINVOICE_ROLES:
        raise HTTPException(
            status_code=403, detail="Finance roles required for e-invoice"
        )

    db = _get_db()

    # Load the order / invoice doc for this id
    order = None
    for collection_name in ("orders", "invoices"):
        try:
            coll = db.get_collection(collection_name)
            doc = coll.find_one(
                {
                    "$or": [
                        {"id": order_id},
                        {"order_id": order_id},
                        {"invoice_id": order_id},
                    ]
                },
                {"_id": 0},
            )
            if doc:
                order = doc
                break
        except Exception:  # noqa: BLE001 -- db not available in test env
            pass

    if order is None:
        raise HTTPException(
            status_code=404, detail=f"Order/invoice {order_id!r} not found"
        )

    # Audit L3-18: the same store rule GET /orders/{id} applies -- a
    # store-scoped finance role never reads (or triggers) another shop's
    # IRN / QR / ack (403; SUPERADMIN/ADMIN pass through).
    # Codex review of #1378: validate_store_access reads a missing store_id
    # as "use my active store" and passes, so an unattributed (legacy) record
    # is HQ-only -- the same per-object rule as can_access_store_scoped.
    store_id = order.get("store_id")
    if not store_id and not can_access_store_scoped(store_id, current_user):
        raise HTTPException(
            status_code=403, detail="This record has no shop; only an admin can open it"
        )
    validate_store_access(store_id, current_user)

    result = await generate_irn(db, order)
    return result
