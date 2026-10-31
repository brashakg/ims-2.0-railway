"""Inventory - a product's one average cost and its cost history (S4).

The product page's cost card (board "Cost price of a product", owner
2026-10-23): pieces held, the average cost, the stock value (pieces x average
cost -- stock_value, the one rule), how many pieces have no cost yet, and the
cost history (each bill acceptance and each correction), never a per-piece
cost. What we pay is the accounts roles' only (cost_mask.AP_ROLES +
SUPERADMIN, owner ruling 2026-10-12): both routes are closed to everyone else.
"""

from ._shared import (
    BaseModel,
    Depends,
    Field,
    HTTPException,
    get_audit_repository,
    get_product_repository,
    get_stock_repository,
    logger,
    require_roles,
    router,
)
from .helpers import _get_db
from ...services import product_cost, stock_value
from ...services.cost_mask import AP_ROLES
from ...services.item_events import on_hand_match
from ...services.name_resolver import stamp_user_names


def _cost_card(product: dict) -> dict:
    stock_repo = get_stock_repository()
    flt = {**on_hand_match(include_reserved=True), "product_id": product["product_id"]}
    units = (stock_repo.find_many(flt, limit=0) or []) if stock_repo is not None else []
    pieces = sum(stock_value._pieces(u) for u in units)
    average = stock_value.average_cost(product)
    history = product_cost.history(product)
    db = _get_db()
    if db is not None:
        try:
            stamp_user_names(db, history, ("by",))  # who accepted / corrected
        except Exception:  # noqa: BLE001 - the id stays readable
            logger.warning("[COST] naming the cost history of %s failed", product["product_id"], exc_info=True)
    return {
        "product_id": product["product_id"],
        "pieces": pieces,
        "average_cost": average or None,
        "stock_value": stock_value.value(pieces, product),
        # "this figure is too low": pieces with no cost add nothing.
        "uncosted_pieces": 0 if average else pieces,
        "history": history,
    }


def _product_or_404(product_id: str) -> dict:
    repo = get_product_repository()
    product = repo.find_by_id(product_id) if repo is not None else None
    if not product or not product.get("product_id"):
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/products/{product_id}/cost")
async def get_product_cost(
    product_id: str,
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """The product's cost card: pieces held (every shop), its one average
    cost, the stock value, uncosted pieces and the cost history (newest
    first)."""
    return _cost_card(_product_or_404(product_id))


class CostCorrection(BaseModel):
    average_cost: float = Field(..., gt=0, le=10_000_000)
    reason: str = Field(..., min_length=3, max_length=300)


@router.post("/products/{product_id}/cost-correction")
async def correct_product_cost(
    product_id: str,
    body: CostCorrection,
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """An accounts correction of a product's average cost (excl. GST), with a
    required reason -- e.g. a supplier's credit note for a scheme discount.
    It is a row of the cost history with the name and the reason."""
    product = _product_or_404(product_id)
    reason = body.reason.strip()
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Say why the cost is being corrected.")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Costs are unavailable; please retry.")
    row = product_cost.correct(db, product_id, body.average_cost, reason, current_user.get("user_id"))
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="The average cost is already that, or it changed meanwhile. Refresh and try again.",
        )
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create({
                "action": "product.cost_correction",
                "entity_type": "product",
                "entity_id": product_id,
                "user_id": current_user.get("user_id"),
                "detail": {"old_cost": row.get("old_cost"), "new_cost": row.get("new_cost"),
                           "reason": reason},
            })
    except Exception:  # noqa: BLE001 - the history row is the record
        logger.warning("[COST] audit of the correction on %s failed", product_id, exc_info=True)
    return _cost_card(_product_or_404(product_id))
