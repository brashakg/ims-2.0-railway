"""Request/response models for the inventory router."""

from ._shared import (
    BaseModel,
    Field,
    List,
    Optional,
    date,
)

# ============================================================================
# SCHEMAS
# ============================================================================


class StockAddRequest(BaseModel):
    product_id: str
    # GENEROUS upper bound: /stock/add mints ONE serialized row per unit in a
    # `for _ in range(quantity)` loop (each iteration = a counter call + a DB
    # insert), so an unbounded quantity (fat-finger or malicious 1e9) floods the
    # DB and hangs the worker. 10k is far above any real single-SKU intake but
    # caps the loop. Mirrors the orders.py C-3 line-quantity guard.
    quantity: int = Field(..., ge=1, le=10000)
    location_code: Optional[str] = None
    batch_code: Optional[str] = None
    lot: Optional[str] = None  # alias accepted alongside batch_code (CL)
    expiry_date: Optional[date] = None


class StockTransferRequest(BaseModel):
    from_store_id: str
    to_store_id: str
    items: List[dict]  # stock_id, quantity


class StockCountItem(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    sku: Optional[str] = None
    counted_quantity: int = Field(..., ge=0)
    notes: Optional[str] = None


class StartStockCountRequest(BaseModel):
    category: Optional[str] = None
    zone: Optional[str] = None
    notes: Optional[str] = None


class CompleteStockCountRequest(BaseModel):
    notes: Optional[str] = None


class QuarantineRequest(BaseModel):
    """Body for PATCH /stock/{stock_id}/quarantine."""

    reason: str = Field(..., min_length=1)
    notes: Optional[str] = Field(default=None, max_length=200)
    rtv_vendor_id: Optional[str] = None


class LiftQuarantineRequest(BaseModel):
    """Body for PATCH /stock/{stock_id}/lift-quarantine. lift_reason is
    MANDATORY (>=5 chars) so a mis-quarantine correction is always justified in
    the immutable audit trail."""

    lift_reason: str = Field(..., min_length=5)
