"""Transfer stubs (the real transfers live in api/routers/transfers.py)."""

from ._shared import (
    Depends,
    Optional,
    Query,
    _INVENTORY_ROLES,
    get_current_user,
    require_roles,
    router,
    uuid,
)
from .models import (
    StockTransferRequest,
)

# ============================================================================
# TRANSFER STUBS (real transfers are in transfers.py router)
# ============================================================================


@router.get("/transfers")
async def list_transfers(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List stock transfers — delegates to /transfers router for full implementation"""
    # This endpoint exists for backwards compatibility; the full transfer
    # workflow lives in transfers.py with approval/picking/shipping states
    return {
        "transfers": [],
        "note": "Use /api/v1/transfers for full transfer management",
    }


@router.post("/transfers")
async def create_transfer(
    request: StockTransferRequest,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Create a stock transfer — delegates to /transfers for full workflow"""
    return {
        "transfer_id": str(uuid.uuid4()),
        "transfer_number": f"TRF-{uuid.uuid4().hex[:6].upper()}",
        "note": "Use /api/v1/transfers for full transfer management",
    }


# BUG-018: the legacy POST /inventory/transfers/{id}/send and
# /inventory/transfers/{id}/receive endpoints were DEAD STUBS -- they returned a
# hardcoded success message and moved NO stock. A caller could "send" or
# "receive" a transfer and get a 200 while both stores' on-hand stayed wrong.
# They were REMOVED (verified no caller: the frontend uses the REAL workflow at
# POST /api/v1/transfers/{id}/ship and /api/v1/transfers/{id}/receive in
# transfers.py, which actually move serialized stock_units). Callers must use the
# real /transfers/* router -- a success response now always means stock moved.
