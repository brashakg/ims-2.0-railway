"""IMS 2.0 - F35 Cost & margin masking (#35).

Cost and margin data (supplier landing prices, unit economics) is commercially
sensitive. This is a PURE read-path filter: it strips `cost_price` and every
derived margin figure from an API response dict for any role not authorised to
see cost. No DB access, no engine imports, no schema change, no state mutation.

Role policy (DECISIONS sec 9, owner rulings D7 / 2026-09-29 / 2026-10-12):
  * EVERYTHING WE PAY (owner ruling 2026-10-12) -- product cost (cost_price,
    landed cost, margin, stock value at cost and every figure worked out from
    it: context="product") AND what was paid to suppliers (PO line prices and
    totals, receiving prices, last price paid, cost at acceptance, a vendor
    return's price and credit, a debit note's rates: context="purchase") --
    is shown to SUPERADMIN / ADMIN / ACCOUNTANT (COST_VISIBLE_ROLES) only.
    AREA_MANAGER, STORE_MANAGER and CATALOG_MANAGER see MRP / selling price,
    never cost; a manager still raises and sends a PO, by quantity only (the
    server prices it: vendors/cockpit.last_paid_prices). The frontend's ONE
    role list, CostCell's COST_ROLES, IS this set.
  * Operational aggregates (default context: analytics, P&L) and supplier
    payments (context="payables": bills, payments, balances, per vendor AND in
    total -- owner ruling 2026-09-29) -- the same accounts roles. The vendor
    AP and TDS gates ARE AP_ROLES, the finance reads of the same money ask
    can_see_cost(user, "payables"), and their rbac_policy rows ARE
    rbac_policy._core.ACCOUNTS, built from it.
  * Who we buy from (context="supplier"): PURCHASE_ROLES -- the people who
    raise POs and receive -- see a vendor's GSTIN, contacts and terms; anyone
    else sees VENDOR_NAME_KEYS only (the vendor list, a debit note's vendor
    block). It is the supplier, not a price.
  * Counter roles (SALES_*, CASHIER, OPTOMETRIST, WORKSHOP_STAFF) see cost in
    no context (audit F46/F60, owner ruling D7). A router never keeps its own
    copy of this rule -- a role set, a key allow-list or a vendor-by-name
    projection: it asks can_see_cost / mask_* here.
    tests/test_what_we_pay_accounts_only.py and section 18 of
    tests/test_counter_roles_no_purchase_reads.py change this rule and fail on
    any read that does not change with it; tests/test_cost_mask_f35.py flags
    the common hand-made shapes.

"Hidden" = the field is removed server-side so it never reaches the browser.
No emoji (Windows cp1252).
"""
from typing import Dict, Iterable, List
import re

# The accounts roles. Defined ONCE, here: they see cost + margin, and they
# alone see supplier payments -- the vendor ledger / bills / payments / debit
# notes / ap-aging / TDS, the purchase-invoice and recon books (their
# require_roles gates ARE this tuple), plus every finance read of the same
# money and vendor rebates (they ask can_see_cost(user, "payables")); the
# rbac_policy rows for all of them are rbac_policy._core.ACCOUNTS, built from
# this tuple. SUPERADMIN passes every gate on its own.
AP_ROLES = ("ADMIN", "ACCOUNTANT")
COST_VISIBLE_ROLES = {"SUPERADMIN", *AP_ROLES}
# The purchase roles: who buys, receives and sends POs. Defined ONCE, here:
# the purchase screens' route gate (routers/vendors/_shared._VENDOR_ROLES) and
# the vendor-return, RTV debit-note and RMA gates ARE this tuple, and the
# "supplier" context (a vendor's GSTIN, contacts, terms) admits exactly it.
# Not what was paid: that is COST_VISIBLE_ROLES (owner ruling 2026-10-12).
# SUPERADMIN passes every require_roles gate on its own.
PURCHASE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")
# Who reads vendor returns and RTV debit notes: the purchase roles plus the
# Vendor Returns screen's WORKSHOP_STAFF (logs the defective pair). Outside
# COST_VISIBLE_ROLES a reader is shown the item, quantity and reason only
# (mask_vendor_return / mask_debit_note). Their read gates ARE this tuple.
RETURN_READERS = (*PURCHASE_ROLES, "WORKSHOP_STAFF")
# Who receives goods into stock -- log, accept, express-receive, void or
# escalate a receipt, its bill-photo upload and the receiving screen. Owner
# ruling 2026-09-28: RECEIVING IS MANAGERS ONLY; the accountant keeps bills,
# payments and the receipt reads. Defined ONCE, here: the receiving gates
# (routers/vendors/_shared._RECEIVE_ROLES) ARE this tuple, their rbac_policy
# rows are rbac_policy._core.RECEIVE, and the frontend RECEIVING_MANAGER_ROLES
# is pinned to it (receivingRoles.test.ts).
RECEIVE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")
# context -> the roles it admits on top of COST_VISIBLE_ROLES. Owner ruling
# 2026-10-12: nobody past the accounts roles sees what we pay, so "product",
# "purchase" and "payables" admit no one else.
_CONTEXT_ROLES = {
    "purchase": set(),
    "product": set(),
    "payables": set(),
    "supplier": set(PURCHASE_ROLES),
}

# Raw cost fields that may appear on product / stock / order-line payloads.
# landed_cost* / moving_avg_cost are what a purchase bill writes onto the
# product master (purchase_invoices.py); purchase_price is the legacy name.
_COST_FIELDS = {
    "cost_price", "cost_value", "cost_at_sale", "unit_cost",
    "landed_cost", "landed_cost_paise", "moving_avg_cost", "purchase_price",
    "estimated_purchase_cost",
    # How many units carry NO cost: it goes with the cost it qualifies (F47
    # stock ledger rows), so the one mask hides it with cost_value.
    "uncosted_units", "valuation_cost_pending", "cost_valuation_pending",
    "cost_price_is_catalogue_only",
    # The GST a bill claiming no credit added to a unit's cost (F04,
    # purchase_invoices._add_unclaimed_gst_to_units) -- a fraction of what we
    # paid -- and the supplier bill a cost came from (H1, GST review
    # 2026-10-19).
    "unclaimed_gst", "cost_source_id",
}
# Derived margin / COGS figures emitted by analytics + finance payloads.
_MARGIN_FIELDS = {
    "margin_pct", "gross_margin", "net_margin", "cogs",
    "gross_margin_pct", "net_margin_pct", "avg_margin_pct",
    "total_cost", "cogs_estimated_lines", "unit_margin", "estimated_margin",
}
# Money worked out from cost (owner ruling D7: counter roles see no figure
# worked out from cost, anywhere): a stock count's variance / shrinkage /
# overage at cost, a stock alert's cost impact and its two totals, a workshop
# remake's spoilage cost, a blind count's unit cost and variance at cost.
_COST_DERIVED_FIELDS = {
    "variance_value", "shrinkage_value", "overage_value",
    "shrinkage_value_written_off",
    "costImpact", "deadStockValue", "recommendedRestockValue",
    "cost_paise", "spoilage_cost_paise", "spoilage_cost_mtd_paise",
    "variance_value_paise", "net_variance_value_paise",
}
_ALL_MASKED = _COST_FIELDS | _COST_DERIVED_FIELDS | _MARGIN_FIELDS
# On a purchase document (a PO, a goods receipt, the receiving cockpit, a
# vendor's purchase history) every money figure is a price paid: the line
# price, its tax and the totals. Generic names that mean a SELLING price on an
# order, so they are hidden on purchase reads only (mask_purchase).
_PURCHASE_PRICE_FIELDS = {
    "unit_price", "estimated_unit_price", "line_total", "line_tax",
    "cgst", "sgst", "igst", "subtotal", "tax", "tax_amount", "total_amount",
    "gst_summary", "totals", "spend", "total_spend", "mtd_spend",
    # an edit's price changes on the order's history (po_detail.update_po)
    "price_detail",
}


def is_cost_key(name) -> bool:
    """True when `name` is a key this module hides as cost, margin or a figure
    worked out from cost. For a reader that looks keys up by a caller-given
    name (a smart-collection rule): matching on a hidden key is reading it."""
    return name in _ALL_MASKED


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


COST_WRITE_REFUSED = (
    "Only the accounts team (admin, accountant) can enter a cost price. "
    "Leave the cost out; it is filled in from the supplier bill."
)


def refuse_cost_write(user: dict, *sent) -> None:
    """THE write side of the product-cost rule (audit FU-23, owner
    2026-10-18): a login that may not SEE product cost may not SET it either.
    Each door that takes a cost from the caller passes what was sent; any value
    that is not None from such a login is a 403 with a plain message, and
    omitting the cost (what the screens do for these roles) is untouched.

    The doors that call it (keep this list in step with the callers):
      * POST /products and PUT /products/{id} -- `cost_price`
        (routers/products.py create_product / update_product);
      * POST /catalog/products and PUT /catalog/products/{id} --
        `pricing.cost_price` (routers/catalog.py);
      * POST /lens-catalog and PATCH /lens-catalog/{id} -- `cost_price`
        (routers/lens_catalog.py);
      * POST /inventory/opening-stock/preview and /commit -- each row's
        `unit_cost`, which mints the units' cost (routers/inventory/
        opening_stock.py).
    The purchase side (PO prices, receiving, bills) has its own gates."""
    if any(v is not None for v in sent) and not can_see_cost(user, "product"):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail=COST_WRITE_REFUSED)


def _strip(value, hide):
    if isinstance(value, dict):
        return {k: _strip(v, hide) for k, v in value.items() if k not in hide}
    if isinstance(value, list):
        return [_strip(v, hide) for v in value]
    return value


def mask_cost(
    doc: dict, user: dict, context: str = "default", extra: Iterable[str] = ()
) -> dict:
    """`doc` without cost + margin fields, at any depth (an order's lines, a
    unit's joined product, a count's variances), unless the caller may see
    cost. `extra` names keys that are a value at cost on THIS response only
    (a stock report's "totalValue", a transfer's "total_value") -- generic
    names that mean something else elsewhere.

    Returns a copy and never edits `doc`: a repository may hand back the
    stored dict itself (the no-Mongo MockCollection does), and masking it
    would strip the cost for the next reader and for the sale."""
    if not isinstance(doc, dict) or can_see_cost(user, context):
        return doc
    return _strip(doc, _ALL_MASKED | set(extra))


def mask_cost_list(
    docs: List[dict], user: dict, context: str = "default", extra: Iterable[str] = ()
) -> List[dict]:
    """mask_cost over a list (e.g. a catalog / inventory page)."""
    if can_see_cost(user, context):
        return docs
    return [mask_cost(d, user, context, extra) if isinstance(d, dict) else d for d in (docs or [])]


# What a goods receipt shows outside the purchase roles -- the barcode trace
# is open to every signed-in role: when and where it came in, what and how
# many. Never who sold it or for which order, the bill number, the bill photo
# (file id / hash) or the accept lock (owner ruling 2026-10-12). Allow-lists,
# so a field added later stays hidden until someone lists it here.
_RECEIPT_KEYS = (
    "grn_id", "grn_number", "grn_subtype", "store_id", "status",
    "received_at", "accepted_at", "created_at",
    "total_received", "total_accepted", "total_rejected",
)
_RECEIPT_ITEM_KEYS = (
    "product_id", "product_name", "sku", "received_qty", "accepted_qty",
    "rejected_qty", "batch_code", "lot_number", "expiry_date", "location_code",
)


# A receipt line's price paid is its cost; not in _COST_FIELDS because an order
# line's unit_price is the selling price.
_RECEIPT_LINE_COST_FIELDS = _ALL_MASKED | {"unit_price"}


def mask_receipt(grn: dict, user: dict) -> dict:
    """The one rule on a goods receipt. To whom -- who sold it, the bill, the
    bill photo, the accept lock -- is the "supplier" context: anyone else sees
    only _RECEIPT_KEYS / _RECEIPT_ITEM_KEYS. What was paid on each line (a
    "Bought without PO" receipt records it as `unit_price`) is the cost of the
    units it minted, so it is the "product" context, as every read of a unit's
    or product's cost: narrowing that one context takes it off every read at
    once. Never edits `grn` (see mask_cost). Every read that returns a receipt
    -- GET /vendors/grn, GET /vendors/grn/{id}, the barcode trace -- goes
    through this."""
    if not isinstance(grn, dict):
        return grn
    paid = can_see_cost(user, "product")
    if can_see_cost(user, "supplier"):
        if paid:
            return grn
        out = mask_cost(grn, user, "product")
        out["items"] = _strip(grn.get("items") or [], _RECEIPT_LINE_COST_FIELDS)
        return out
    item_keys = _RECEIPT_ITEM_KEYS + (("unit_price",) if paid else ())
    out = _pick(grn, _RECEIPT_KEYS)
    out["items"] = [_pick(it, item_keys) for it in grn.get("items") or []]
    return out


def mask_fields(doc: Dict, user: dict, context: str = "default") -> Dict:
    """Alias for masking an aggregate payload (e.g. a P&L dict)."""
    return mask_cost(doc, user, context)


def mask_purchase(doc, user: dict):
    """A purchase document (or a list of them) without any price paid -- the
    cost fields plus _PURCHASE_PRICE_FIELDS, at any depth -- unless the caller
    may see what we pay (owner ruling 2026-10-12). Quantities, products,
    statuses and dates stay."""
    if can_see_cost(user, "purchase"):
        return doc
    if isinstance(doc, list):
        return [mask_purchase(d, user) for d in doc]
    return _mask_legacy_purchase_history(mask_cost(doc, user, "purchase", extra=_PURCHASE_PRICE_FIELDS))


def _mask_legacy_purchase_history(value):
    """Old PO edits embedded paid prices in prose before price_detail existed."""
    if isinstance(value, list):
        return [_mask_legacy_purchase_history(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _mask_legacy_purchase_history(item) for key, item in value.items()}
    detail = result.get("detail")
    if result.get("kind") == "edited" and isinstance(detail, str):
        parts = detail.split("; ")
        safe = [part for part in parts if not re.search(r"\bcost\s+Rs\b", part, re.IGNORECASE)]
        if len(safe) != len(parts):
            result["detail"] = "; ".join(safe) or "price changed"
    return result


def _pick(doc, keys) -> Dict:
    return {k: doc[k] for k in keys if k in doc} if isinstance(doc, dict) else {}


# The vendor by name: all anyone outside the purchase roles sees of a supplier
# -- on the vendor list (the workshop job, vendor returns and the buy desk pick
# one by name, and search only these keys) and on a debit note's vendor block.
# Never its GSTIN, contacts, address, bank details or terms (D7).
VENDOR_NAME_KEYS = (
    "vendor_id", "vendor_code", "legal_name", "trade_name", "name", "is_active",
)


def mask_vendor(doc: Dict, user: dict) -> Dict:
    """A vendor by name only, unless the caller is a purchase role."""
    if not isinstance(doc, dict) or can_see_cost(user, "supplier"):
        return doc
    return _pick(doc, VENDOR_NAME_KEYS)


# What a vendor return / RTV debit note shows to anyone who may not see what we
# pay: the item, quantity and reason (owner rulings 2026-09-29 / 2026-10-12).
# Allow-lists, so a money field added later stays hidden until someone lists
# it here.
_RETURN_KEYS = (
    "return_id", "vendor_id", "vendor_name", "store_id", "return_type",
    "status", "notes", "status_history",
    "courier_name", "tracking_number", "tracking_url", "shipped_at",
    "created_at", "created_by", "updated_at", "updated_by",
    "receipt_rejection", "grn_id", "grn_number", "grn_line_index",
    "debit_note_draft_status", "debit_note_draft_id",
    # The pieces going back (ids, barcodes, "no receipt" flag): goods, not
    # money or supplier details -- the managers check what is in the box.
    "stock_ids", "pieces",
)
# A return LINE -- a vendor return's item or an RMA's line -- as anyone outside
# the accounts roles reads it (owner ruling 2026-10-12, R3): the item, the
# quantity and the reason, never a unit price, rate or line value under any
# name. ONE allow-list for both kinds of return (return_line_for_reader), so a
# price key added later under a new name stays hidden on both.
_RETURN_ITEM_KEYS = ("product_id", "product_name", "quantity", "reason")
_DEBIT_NOTE_KEYS = (
    "debit_note_id", "debit_note_number", "financial_year", "issue_date",
    "entity_id", "store_id", "seller", "rtv_ref", "rtv_ref_id",
    "created_at", "created_by",
)
_DEBIT_NOTE_LINE_KEYS = ("sku", "description", "hsn", "qty")


def return_line_for_reader(line: Dict) -> Dict:
    """One return line (a vendor return's item, an RMA's line) cut to the
    allow-list a non-accounts reader gets. The caller has already asked the
    'payables' rule: mask_vendor_return here, payables_mask.strip_rma_credit
    for an RMA."""
    return _pick(line, _RETURN_ITEM_KEYS)


def mask_vendor_return(doc: Dict, user: dict) -> Dict:
    """A vendor return without the price paid, its total, the credit amount or
    the supplier's credit-note / bill reference, unless the caller may see
    what we pay."""
    if isinstance(doc, dict) and "credit_confirmation_fence" in doc:
        doc = {k: v for k, v in doc.items() if k != "credit_confirmation_fence"}
    if not isinstance(doc, dict) or can_see_cost(user, "purchase"):
        return doc
    out = _pick(doc, _RETURN_KEYS)
    out["items"] = [return_line_for_reader(it) for it in doc.get("items") or []]
    return out


# A vendor RMA (routers/vendor_rma) or the reply to one of its steps: the
# return's workflow without its unit costs, expected / received credit,
# variance, write-off or the supplier's credit notes. Each step of its history
# keeps who, when and the new status only: the notes the steps write carry the
# credit and the write-off in words ("credit note CN-1 for 432177 paise").
_RMA_KEYS = (
    "ok", "error", "rma_id", "vendor_id", "vendor_name", "store_id", "po_id",
    "grn_id", "return_id", "status", "vendor_rma_number", "courier", "notes",
    "fully_reconciled", "created_at", "created_by", "updated_at",
    "closed_at", "closed_by",
)
_RMA_STEP_KEYS = ("status", "at", "by")


def mask_rma(doc: Dict, user: dict) -> Dict:
    """A vendor RMA, a step's reply or a refused step's error without anything
    we pay, unless the caller may see what we pay."""
    if not isinstance(doc, dict) or can_see_cost(user, "purchase"):
        return doc
    out = _pick(doc, _RMA_KEYS)
    if "lines" in doc:
        out["lines"] = [_pick(ln, _RETURN_ITEM_KEYS) for ln in doc.get("lines") or []]
    if "status_history" in doc:
        out["status_history"] = [_pick(s, _RMA_STEP_KEYS) for s in doc.get("status_history") or []]
    return out


def mask_debit_note(doc: Dict, user: dict) -> Dict:
    """An RTV debit note without rates, taxable values, tax, totals, the
    supplier's GSTIN / address / state or its bill number, unless the caller
    may see what we pay. Our own (seller) block is on every invoice we print."""
    if not isinstance(doc, dict) or can_see_cost(user, "purchase"):
        return doc
    out = _pick(doc, _DEBIT_NOTE_KEYS)
    out["vendor"] = _pick(doc.get("vendor"), VENDOR_NAME_KEYS)
    out["lines"] = [_pick(ln, _DEBIT_NOTE_LINE_KEYS) for ln in doc.get("lines") or []]
    return out
