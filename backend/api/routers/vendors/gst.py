"""Purchase-order GST engine: party resolution, line rates, PO tax build."""

from ._shared import _pm, get_store_repository, get_vendor_repository, logger


# How far along the purchase a cost figure came from. A LATER step may correct
# an EARLIER one: the PO rate only provisionally agrees a price (owner addendum
# 2026-08-26 -- it exists to unblock cataloguing), and the goods actually
# received settle it ("goods receipt sets the real cost anyway"). A source that
# is not ranked here -- a cost a person typed, an import, a legacy row with no
# source at all -- is NEVER overwritten by this helper, only by an explicit
# edit. The rule lives on the SOURCES, not on the caller, so there is no wiring
# at a call site that could silently be passed the wrong thing.
_PO_PROVISIONAL_COST_SOURCE = "PO_RATE"
_COST_SOURCE_RANK = {_PO_PROVISIONAL_COST_SOURCE: 1, "GRN_PO": 2}


def _promote_cost_from_rate(product_id, prod, unit_cost, source, product_repo) -> bool:
    """Fill a product's missing cost_price from an agreed line rate.

    ONE place for the two moments a purchase reveals a cost: the rate typed on
    a purchase order (owner ruling 2026-08-26 -- "the PO rate IS the cost", so
    raising the PO COMPLETES the cataloguing instead of being blocked by it) and
    the rate on the goods receipt that follows.

    Writes into an EMPTY cost, or over a cost whose source this one OUTRANKS
    (see _COST_SOURCE_RANK). Cost feeds margin and stock valuation, so every
    other existing figure is left exactly as it is. Restamps the catalogue
    status atomically, so a product that was DRAFT for the single reason
    "no cost" turns ACTIVE the moment a rate is agreed. Returns True when a cost
    was written. Fail-soft: never raises.
    """
    if product_repo is None or not prod or not product_id:
        return False
    try:
        cost = round(float(unit_cost or 0), 2)
    except (TypeError, ValueError):
        return False
    if cost <= 0:
        return False
    if prod.get("cost_price"):
        held = _COST_SOURCE_RANK.get(str(prod.get("cost_source") or ""))
        mine = _COST_SOURCE_RANK.get(str(source or ""))
        if held is None or mine is None or mine <= held:
            return False
    try:
        product_repo.update(product_id, {"cost_price": cost, "cost_source": source})
        _pm.apply_restamp_atomic(
            product_id, prod, {"cost_price": cost}, product_repo=product_repo
        )
    except Exception as exc:  # noqa: BLE001 - a cost promote never blocks the PO/GRN
        logger.warning("[VENDOR] cost promote skipped for %s: %s", product_id, exc)
        return False
    return True


def _po_gst_parties(vendor, store_doc) -> dict:
    """Decide the PLACE OF SUPPLY for a purchase order: the vendor supplies, the
    delivery store receives.

    Same-state supply -> CGST + SGST. Different states -> IGST. This business
    runs 3 legal entities across 4 GSTINs in 2 states, so "our state" is NOT a
    constant.

    THE SAME ENGINE THE PURCHASE BILL USES. determine_place_of_supply /
    state_code_of in services/purchase_invoice_engine.py already classify the
    vendor's bill; the order that precedes it now calls exactly those, so the
    order and the bill cannot return opposite verdicts on one purchase.

    One thing this passes that the bill does not: the delivery store's OWN
    declared state, as the explicit place of supply. Place of supply for goods
    is where delivery terminates -- and stores.py `_derive_store_gstin` falls
    back to the entity's PRIMARY registration when the entity holds none in the
    store's state, so a Maharashtra shop can be stamped with a Jharkhand GSTIN.
    Reading the shop's own state first keeps the physical delivery, not the
    paperwork fallback, in charge.

    Returns the same FIELD NAMES the bill writes (`supply_place_recipient`,
    `supplier_state`, `interstate`) with the same meanings. There is
    deliberately NO bare `place_of_supply` key: on a bill that name means the
    SUPPLIER state (itc_reconcile keys on it), so a PO carrying it under the
    recipient meaning would flip every inter-state purchase to intra-state the
    day anything copied it onto a bill.
    """
    from ...services import purchase_invoice_engine as pinv

    # NB: `store_doc`, never `store` -- this module binds the name `store` to a
    # get_file_store() handle, and the file-store guard in
    # test_users_auth_hardening resolves handles by NAME across the whole file,
    # so a dict called `store` here reads as an unscoped read of the bucket that
    # also holds employee Aadhaar/PAN scans.
    vendor_doc = vendor if isinstance(vendor, dict) else {}
    store_doc = store_doc if isinstance(store_doc, dict) else {}
    vendor_gstin = str(vendor_doc.get("gstin") or "").strip()
    store_gstin = str(store_doc.get("gstin") or "").strip()
    store_declared = pinv.state_code_of(
        store_doc.get("state_code")
    ) or pinv.state_code_of(store_doc.get("state"))
    pos, interstate = pinv.determine_place_of_supply(
        vendor_gstin, store_gstin, store_declared
    )
    supplier_state = pinv.state_code_of(vendor_gstin)
    return {
        "vendor_gstin": vendor_gstin,
        "supplier_state": supplier_state or None,
        "store_gstin": store_gstin,
        "supply_place_recipient": pos,
        "interstate": interstate,
        # True when we could not prove the classification from GST numbers, so
        # the screen says "shown as within-state" instead of asserting it.
        "supply_place_assumed": not (supplier_state and pos),
    }


def _po_line_gst_rate(line, prod) -> tuple:
    """Rate (percent) for ONE purchase-order line, HSN-first, and what is
    missing when it cannot be settled.

    ``line`` is a plain dict (every door hands one in: the manual form dumps its
    pydantic model, the two auto-drafters build theirs). Returns
    ``(rate, hsn, source, missing)``. ``rate`` is None ONLY when nothing could
    settle it -- and then ``missing`` says why in plain English and the line is
    stored UNRESOLVED with zero tax rather than taxed at a guessed rate.

    Order: an explicit rate on the request wins; otherwise the line's / the
    product's HSN is resolved against the owner-editable HSN table (so a GST
    revision flows through without a code change); otherwise the rate stamped on
    the product when it was catalogued; otherwise unresolved.
    """
    from ...services.gst_rates import resolve_gst_rate_strict

    line = line if isinstance(line, dict) else {}
    prod = prod if isinstance(prod, dict) else {}
    hsn = line.get("hsn") or prod.get("hsn_code")
    given = line.get("gst_rate")
    if given is not None:
        return given, hsn, "line", None
    rate, missing = resolve_gst_rate_strict(hsn)
    if rate is not None:
        return rate, hsn, "hsn", None
    catalogued = prod.get("gst_rate")
    if catalogued is not None:
        try:
            # The rate settled when the product was catalogued. Not a guess --
            # a person chose it -- so the line IS taxed. `missing` still travels
            # with it so the screen can say why the HSN alone did not settle it.
            return float(catalogued), hsn, "catalogue", missing
        except (TypeError, ValueError):
            pass
    return None, hsn, "", missing


def build_po_gst(raw_lines, product_of, vendor, store_doc) -> dict:
    """THE per-line GST computation for EVERY door that writes a purchase order.

    Three doors write into `purchase_orders`: the manual Create-PO form
    (`create_po` below), the per-power CL/lens auto-draft
    (`routers/cl_po.py::generate_cl_po`) and the demand-forecast auto-draft
    (`create_po_from_forecast` below). The last two used to book a flat
    ``subtotal * 0.18`` and store lines with NO `tax_rate` at all, so a
    contact-lens order was over-taxed by 13 points AND the bill later drafted
    off it (`purchase_invoice_engine.lines_from_grn` reads `po_line["tax_rate"]`)
    charged 0%. Both bugs came from the rule living in one caller instead of in
    one place, so it lives HERE and every door calls it.

    ``raw_lines``  -- list of dicts, each with at least product_id / quantity /
                      unit_price, optionally hsn / gst_rate. Every other key on
                      the line (power cell, description, sku...) is carried
                      through onto the stored item untouched.
    ``product_of`` -- callable(product_id) -> product dict or None. The doors
                      differ in where a product lives (products spine, lens
                      catalog), so each hands in its own lookup.

    Returns {items, subtotal, tax, total, gst_summary, parties, interstate,
    warnings}. `warnings` names EVERY line whose HSN could not settle the rate,
    including the ones that were taxed anyway from the catalogue rate -- HSN is
    mandatory on a GST purchase document, so "taxed" is not the same as "fine".
    """
    from ...services.gst_rates import split_gst

    parties = _po_gst_parties(vendor, store_doc)
    interstate = parties["interstate"]

    items = []
    warnings = []
    subtotal = 0.0
    tax = 0.0
    for raw in raw_lines or []:
        line = dict(raw) if isinstance(raw, dict) else {}
        try:
            qty = float(line.get("quantity") or 0)
            unit_price = float(line.get("unit_price") or 0)
        except (TypeError, ValueError):
            qty, unit_price = 0.0, 0.0
        line_total = round(qty * unit_price, 2)
        prod = None
        if callable(product_of):
            try:
                prod = product_of(line.get("product_id"))
            except Exception as exc:  # noqa: BLE001 - a lookup miss is not fatal
                logger.warning("[VENDOR] PO product lookup failed: %s", exc)
        rate, hsn, source, missing = _po_line_gst_rate(line, prod)
        line_tax = round(line_total * ((rate or 0.0) / 100.0), 2)
        cgst, sgst, igst = split_gst(line_tax, interstate)
        subtotal += line_total
        tax += line_tax
        if missing:
            warnings.append(
                {
                    "product_id": line.get("product_id"),
                    "product_name": line.get("product_name"),
                    "missing": missing,
                    # False = no rate at all, so the line carries zero tax.
                    # True = taxed from the catalogue rate, but the HSN is
                    # still missing/unusable and the document needs one.
                    "taxed": rate is not None,
                }
            )
        items.append(
            {
                **line,
                "tax_rate": rate if rate is not None else 0.0,
                "gst_source": source,
                "gst_unresolved": rate is None,
                "gst_missing": missing,
                "hsn": hsn,
                "line_tax": line_tax,
                "cgst": cgst,
                "sgst": sgst,
                "igst": igst,
                "ordered_qty": line.get("quantity"),
                "received_qty": 0,
                "line_status": "OPEN",
            }
        )

    subtotal = round(subtotal, 2)
    tax = round(tax, 2)
    return {
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": round(subtotal + tax, 2),
        "gst_summary": {
            "cgst": round(sum(i["cgst"] for i in items), 2),
            "sgst": round(sum(i["sgst"] for i in items), 2),
            "igst": round(sum(i["igst"] for i in items), 2),
            "tax": tax,
        },
        "parties": parties,
        "interstate": interstate,
        "warnings": warnings,
    }


def po_gst_context(store_id, vendor_id):
    """(vendor_doc, store_doc) for a PO's place-of-supply decision. Fail-soft:
    a missing repo/doc degrades to None, which build_po_gst reads as
    'assumed intra-state' rather than inventing a state."""
    vendor_doc = None
    store_doc = None
    try:
        vendor_repo = get_vendor_repository()
        if vendor_repo is not None and vendor_id:
            vendor_doc = vendor_repo.find_by_id(vendor_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR] PO vendor lookup failed: %s", exc)
    try:
        store_repo = get_store_repository()
        if store_repo is not None and store_id:
            store_doc = store_repo.find_by_id(store_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR] PO store lookup failed: %s", exc)
    return vendor_doc, store_doc
