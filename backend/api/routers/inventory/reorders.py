"""THE Reorder list (stock redesign S5): GET /inventory/reorder-list.

One list replaces Buy Desk, Replenishment and Low stock (owner 2026-10-22/23).
The numbers are services/reorder_list (THE buy rule, reorder_policy.buy_qty);
this route only scopes the shop and masks cost. Drafting the orders reuses the
one PO door (POST /vendors/purchase-orders), one draft per vendor.
"""

from ._shared import Depends, Optional, Query, require_roles, router
from .helpers import _get_db
from ...dependencies import is_no_shop_scope, store_in_view
from ...services import reorder_list
from ...services.cost_mask import mask_cost

# Who reads the list: the stock managers, the catalogue manager and the PO
# raisers (the Buy Desk's readers). SUPERADMIN always passes.
REORDER_LIST_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER", "ACCOUNTANT")


@router.get("/reorder-list")
async def get_reorder_list(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*REORDER_LIST_ROLES)),
):
    """{store_id, rows, skipped}. A non-admin reads its own shop (403 on
    another); an admin with no shop named reads every shop. Cost (unit_cost,
    est_value) only for the roles that see cost."""
    shop = store_in_view(store_id, current_user)
    if is_no_shop_scope(shop):
        return {"store_id": None, "rows": [], "skipped": []}
    body = reorder_list.build(_get_db(), store_id=shop)
    return mask_cost({"store_id": shop, **body}, current_user, extra=("est_value",))
