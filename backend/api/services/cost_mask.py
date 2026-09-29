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
  * The buyers (AREA_MANAGER / STORE_MANAGER, with ADMIN / ACCOUNTANT the
    purchase roles) see what was paid where they buy: purchase documents
    (context="purchase") and the product master that prefills a PO
    (context="product", which also admits the CATALOG_MANAGER product form).
    Counter roles (SALES_*, CASHIER, OPTOMETRIST, WORKSHOP_STAFF) never do
    (audit F46/F60, owner ruling D7 + 2026-09-29): a vendor return / RTV debit
    note shows them the item, quantity and reason only, and the vendor list
    (routers/vendors/master.py) names only -- all three ask can_see_cost(user,
    "purchase"), so who sees what was paid and to whom is decided here once.

"Hidden" = the field is removed server-side so it never reaches the browser.
No emoji (Windows cp1252).
"""
from typing import Dict, List

COST_VISIBLE_ROLES = {"SUPERADMIN", "ADMIN", "ACCOUNTANT"}
CATALOG_FORM_ROLES = {"CATALOG_MANAGER"}
_BUYER_ROLES = {"AREA_MANAGER", "STORE_MANAGER"}
# context -> the roles it admits on top of COST_VISIBLE_ROLES.
_CONTEXT_ROLES = {
    "catalog_edit": CATALOG_FORM_ROLES,
    "purchase": _BUYER_ROLES,
    "product": _BUYER_ROLES | CATALOG_FORM_ROLES,
}

# Raw cost fields that may appear on product / stock / order-line payloads.
# landed_cost* / moving_avg_cost are what a purchase bill writes onto the
# product master (purchase_invoices.py); purchase_price is the legacy name.
_COST_FIELDS = {
    "cost_price", "cost_value", "cost_at_sale", "unit_cost",
    "landed_cost", "landed_cost_paise", "moving_avg_cost", "purchase_price",
}
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
    return bool(roles & (COST_VISIBLE_ROLES | _CONTEXT_ROLES.get(context, set())))


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


def _pick(doc, keys) -> Dict:
    return {k: doc[k] for k in keys if k in doc} if isinstance(doc, dict) else {}


# What a vendor return / RTV debit note shows outside the purchase roles: the
# item, quantity and reason (owner ruling 2026-09-29). Allow-lists, so a money
# field added later stays hidden until someone lists it here.
_RETURN_KEYS = (
    "return_id", "vendor_id", "vendor_name", "store_id", "return_type",
    "status", "credit_note_number", "notes", "status_history",
    "courier_name", "tracking_number", "tracking_url", "shipped_at",
    "created_at", "created_by", "updated_at", "updated_by",
)
_RETURN_ITEM_KEYS = ("product_id", "product_name", "quantity", "reason")
_DEBIT_NOTE_KEYS = (
    "debit_note_id", "debit_note_number", "financial_year", "issue_date",
    "entity_id", "store_id", "seller", "rtv_ref", "rtv_ref_id",
    "created_at", "created_by",
)
_DEBIT_NOTE_LINE_KEYS = ("sku", "description", "hsn", "qty")


def mask_vendor_return(doc: Dict, user: dict) -> Dict:
    """A vendor return without the price paid, its total, the credit amount or
    any supplier bill reference, unless the caller is a purchase role."""
    if not isinstance(doc, dict) or can_see_cost(user, "purchase"):
        return doc
    out = _pick(doc, _RETURN_KEYS)
    out["items"] = [_pick(it, _RETURN_ITEM_KEYS) for it in doc.get("items") or []]
    return out


def mask_debit_note(doc: Dict, user: dict) -> Dict:
    """An RTV debit note without rates, taxable values, tax, totals, the
    supplier's GSTIN / address / state or its bill number, unless the caller is
    a purchase role. Our own (seller) block is on every invoice we print."""
    if not isinstance(doc, dict) or can_see_cost(user, "purchase"):
        return doc
    out = _pick(doc, _DEBIT_NOTE_KEYS)
    out["vendor"] = _pick(doc.get("vendor"), ("vendor_id", "name"))
    out["lines"] = [_pick(ln, _DEBIT_NOTE_LINE_KEYS) for ln in doc.get("lines") or []]
    return out
