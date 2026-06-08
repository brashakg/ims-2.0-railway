"""IMS 2.0 - F35 Cost & margin masking (#35).

Cost and margin data (supplier landing prices, unit economics) is commercially
sensitive. This is a PURE read-path filter: it strips `cost_price` and every
derived margin figure from an API response dict for any role not authorised to
see cost. No DB access, no engine imports, no schema change, no state mutation.

Role policy (DECISIONS sec 9):
  * SUPERADMIN / ADMIN / ACCOUNTANT -- always see cost + margin.
  * CATALOG_MANAGER -- sees cost ONLY in the product create/edit form context
    (context="catalog_edit"), never on operational views (inventory ledger, reports).
  * AREA_MANAGER and below (STORE_MANAGER, OPTOMETRIST, SALES_*, WORKSHOP_STAFF)
    -- cost + margin are stripped from the payload; the FE renders "-".

"Hidden" = the field is removed server-side so it never reaches the browser.
No emoji (Windows cp1252).
"""
from typing import Dict, List

COST_VISIBLE_ROLES = {"SUPERADMIN", "ADMIN", "ACCOUNTANT"}
CATALOG_FORM_ROLES = {"CATALOG_MANAGER"}

# Raw cost fields that may appear on product / stock / order-line payloads.
_COST_FIELDS = {"cost_price", "cost_value", "cost_at_sale", "unit_cost"}
# Derived margin / COGS figures emitted by analytics + finance payloads.
_MARGIN_FIELDS = {
    "margin_pct", "gross_margin", "net_margin", "cogs",
    "gross_margin_pct", "net_margin_pct", "avg_margin_pct",
    "total_cost", "cogs_estimated_lines",
}
_ALL_MASKED = _COST_FIELDS | _MARGIN_FIELDS


def _roles_of(user: dict) -> set:
    """Tolerant role extraction: `roles` list, else the single `activeRole`."""
    user = user or {}
    roles = user.get("roles")
    if not roles:
        ar = user.get("activeRole") or user.get("active_role")
        roles = [ar] if ar else []
    return {r for r in roles if r}


def can_see_cost(user: dict, context: str = "default") -> bool:
    roles = _roles_of(user)
    if roles & COST_VISIBLE_ROLES:
        return True
    if context == "catalog_edit" and (roles & CATALOG_FORM_ROLES):
        return True
    return False


def mask_cost(doc: dict, user: dict, context: str = "default") -> dict:
    """Strip cost + margin fields from `doc` (in place) unless the caller may see
    cost. Also handles a nested `pricing.cost_price`. Returns `doc`."""
    if not isinstance(doc, dict) or can_see_cost(user, context):
        return doc
    for field in _ALL_MASKED:
        doc.pop(field, None)
    pricing = doc.get("pricing")
    if isinstance(pricing, dict):
        for field in _ALL_MASKED:
            pricing.pop(field, None)
    return doc


def mask_cost_list(docs: List[dict], user: dict, context: str = "default") -> List[dict]:
    """mask_cost over a list (e.g. a catalog / inventory page)."""
    if can_see_cost(user, context):
        return docs
    return [mask_cost(d, user, context) if isinstance(d, dict) else d for d in (docs or [])]


def mask_fields(doc: Dict, user: dict, context: str = "default") -> Dict:
    """Alias for masking an aggregate payload (e.g. a P&L dict) in place."""
    return mask_cost(doc, user, context)
