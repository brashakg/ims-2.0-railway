"""GSTR-3B (summary return): credit notes, RCM, computation, endpoints."""

from fastapi import Depends, HTTPException, Query
from typing import Optional
from ...utils.event_time import parse_event_utc_naive
from ...utils.ist import (
    instant_in_window_query,
    ist_month_window_utc,
)
from calendar import monthrange
from ..auth import require_roles
from ...dependencies import (
    NO_SHOP_DETAIL,
    is_no_shop_scope,
    resolve_store_scope,
)
from ...services.cost_mask import AP_ROLES
from ...services.payables_mask import strip_input_tax
from ._shared import (
    logger,
    router,
)
from .gst_base import (
    CUSTOMER_SUPPLY_FIELDS,
    real_sale_match,
    UnfileableOrder,
    _get_raw_db,
    _normalise_period,
    _order_rate_lines,
    _order_supply,
    _order_taxable_and_tax,
    _rate_heads,
)
from ...services.gst_rates import split_gst
from .gst_itc import (
    _cn_foreign_store,
    _cn_parent_held,
    _gstin_bound,
    _itc_from_vendor_bills,
    _itc_gstin_from_vendor_bills,
    _itc_match,
    _unallocated_legacy_bills,
    _itc_store_scope,
    _no_credit_inward,
    _placement,
    _sum_heads,
    _ledger_row_return_doc,
    _order_held_off_returns,
    net_cash_due,
    _return_interstate_flag,
    _transfer_outward_bills,
)

# Owner ruling 2026-10-07 (R1): GSTR-3B carries input tax summed from supplier
# bills, so the report and its portal file are the accounts roles' alone
# (cost_mask.AP_ROLES + SUPERADMIN); a manager is refused. Table 4 (ITC),
# 3.1(d) (RCM) and the cash figure also pass services/payables_mask.
# strip_input_tax (R5, keys and rule there) for a reader outside that rule.


def _return_shop(store_id: Optional[str], current_user: dict) -> str:
    """The shop whose GSTR-3B this is, by the ONE shop rule
    (api.dependencies.resolve_store_scope): a named shop must be one of the
    caller's (403 otherwise); omitted -> the caller's own shop (a multi-store
    login with no active one: its first own shop, never another's).

    R4 (owner ruling 2026-10-12): a non-admin login with NO shop sees NO shop.
    A return is ONE shop's document -- there is no empty one to hand back (a
    zero return reads as a nil return, and the portal file would be one) --
    so such a login is refused in the owner's words, named shop or not. It
    used to fall through to store-001 and read that shop's return. ADMIN /
    SUPERADMIN keep their old default: the active shop, else store-001.
    """
    scope = resolve_store_scope(store_id, current_user)
    if is_no_shop_scope(scope):
        raise HTTPException(status_code=403, detail=NO_SHOP_DETAIL)
    return scope or current_user.get("active_store_id") or "store-001"


def _credit_note_totals(db, active_store, year, mon, last_day):
    """(igst, cgst, sgst, taxable) of credit notes issued in the period.

    GSTR-3B Table 3.1(a) is reported NET of credit notes: goods that came back
    are not an outward supply. Nothing subtracted them, so the payable figure
    the accountant reads - and re-types into the portal - was gross of every
    refund. He paid tax on money he had handed back.

    Reads BOTH sources, because neither alone is complete:
      * credit_note_ledger - store-credit refunds, Shopify refunds, superadmin
        credit notes. It is a STORE-CREDIT ledger, so a cash / UPI / card
        refund is correctly absent from it.
      * returns.gst_breakup - the tax event itself, present for every refund
        whatever the tender. Measured on production: all 20 completed returns
        had no ledger row, and Rs 3,209.96 of output tax was never reversed.
    Deduped on return_id so a refund that DID mint store credit is counted
    once; double-counting here would over-reverse and UNDER-declare, which is
    worse than the bug being fixed. A read failure RAISES (M7): a missed
    refund overstates the tax, a missed dedup understates it.
    """
    igst = cgst = sgst = taxable = 0.0
    if db is None:
        return 0.0, 0.0, 0.0, 0.0
    from_dt, to_dt = ist_month_window_utc(year, mon)

    # Which returns already have a credit-note ledger row -- scanned WITHOUT a
    # date window, deliberately.
    #
    # Building this set from the IN-WINDOW ledger rows only (what it did) meant
    # a return whose ledger row landed in a different month was invisible to
    # the dedup: the returns leg counted it in the month of the return, and the
    # ledger leg counted it again in the month of the credit note. One refund,
    # tax reversed twice, and an under-declaration - exactly the failure this
    # function's dedup exists to prevent. A refund taken on the 31st whose
    # store credit is minted the next morning is an ordinary shop event, not an
    # edge case. Attribution follows the ledger row, which IS the credit note.
    seen_returns = set()
    for _row in db.get_collection("credit_note_ledger").find(
        {"store_id": active_store, "type": "ISSUED"}, {"ref": 1, "reason": 1}
    ):
        for _f in ("ref", "reason"):
            for _tok in str(_row.get(_f) or "").replace(",", " ").split():
                if _tok.startswith("RET-"):
                    seen_returns.add(_tok.strip(".:;"))

    # ONE head-resolver state for BOTH legs (parent-order stamp first, customer
    # state as the fallback -- _return_interstate_flag). The ledger leg used to
    # hardcode intra-state for any row without a bool `interstate` stamp,
    # reversing CGST/SGST on a sale that was filed -- and stays, in GSTR-1 --
    # under IGST: the entity kept paying IGST on goods that came back, and the
    # max(0, ...) clamp downstream swallowed the CGST/SGST over-reversal.
    order_inter: dict = {}
    held: dict = {}  # order_id -> parent held on the seller check
    st = db.get_collection("stores").find_one({"store_id": active_store}) or {}

    ledger = db.get_collection("credit_note_ledger")
    if ledger is not None:
        for row in ledger.find(
            {
                "store_id": active_store,
                "type": "ISSUED",
                # Half-open [from_dt, to_dt): the month's last microsecond
                # is IN, next month's first is OUT (audit R03). Rows are
                # ISO strings (naive-UTC legacy or '+00:00') or BSON dates.
                **instant_in_window_query("created_at", from_dt, to_dt),
            }
        ):
            for f in ("ref", "reason"):
                for tok in str(row.get(f) or "").replace(",", " ").split():
                    if tok.startswith("RET-"):
                        seen_returns.add(tok.strip(".:;"))
            t = row.get("tax")
            if t is None:
                gross = float(row.get("gross_refund") or 0.0)
                net = float(row.get("net_refund", row.get("amount", 0)) or 0.0)
                t = gross - net if gross > 0 else 0.0
            t = round(float(t or 0.0), 2)
            tv = row.get("taxable")
            tv = round(float(tv if tv is not None else row.get("amount", 0) or 0.0), 2)
            ret_doc = _ledger_row_return_doc(db, row)
            if _cn_foreign_store(ret_doc, active_store):
                # Legacy cashier-store booking: the sale store's report
                # owns this reversal (its returns leg counts the return
                # doc). Counting it here too reversed one refund under
                # two GSTINs.
                continue
            if _cn_parent_held(db, ret_doc, held):
                # The refund of a sale held off the returns on the seller
                # check (GSTR-1 files neither): no reversal of output tax
                # that was never declared.
                continue
            inter = row.get("interstate")
            if not isinstance(inter, bool):
                inter = _return_interstate_flag(
                    db,
                    ret_doc or {"customer_id": row.get("customer_id")},
                    st,
                    order_inter,
                )
            c, sg, i = split_gst(t, inter)
            igst += i
            cgst += c
            sgst += sg
            taxable += tv

    # A refund must reverse the SAME head the sale was filed under. Deriving it
    # afresh from the customer's state would re-answer a question the parent
    # order already answered - and answer it differently for an online order,
    # whose buyer record is minted stateless. So prefer the parent order's own
    # `interstate` stamp, exactly as the CDNR rows and Table 3.1(a) do.
    # Caught by mutation testing: hardcoding returns as intra-state reversed
    # CGST/SGST on a sale that sat in IGST, leaving BOTH heads wrong.
    # (`order_inter` + the shop doc `st` are shared with the ledger leg above.)
    returns_col = db.get_collection("returns")
    if returns_col is not None:
        for ret in returns_col.find(
            {
                "store_id": active_store,
                "status": {"$in": ["COMPLETED", "completed"]},
            }
        ):
            rid = str(ret.get("return_id") or "")
            if rid and rid in seen_returns:
                continue
            # Parse to the naive-UTC instant whatever the stored shape
            # (BSON naive or aware, ISO string with or without offset),
            # then the half-open month test. Comparing an aware datetime
            # to the naive bound used to raise and silently drop the row.
            when = parse_event_utc_naive(ret.get("created_at"))
            if when is None or not (from_dt <= when < to_dt):
                continue
            gb = ret.get("gst_breakup") or {}
            if not isinstance(gb, dict):
                continue
            t = round(float(gb.get("tax") or 0.0), 2)
            tv = round(float(gb.get("taxable") or 0.0), 2)
            if t <= 0 and tv <= 0:
                continue
            if _cn_parent_held(db, ret, held):  # its sale was not filed
                continue
            c, sg, i = split_gst(t, _return_interstate_flag(db, ret, st, order_inter))
            igst += i
            cgst += c
            sgst += sg
            taxable += tv

    return round(igst, 2), round(cgst, 2), round(sgst, 2), round(taxable, 2)


def _transfer_outward_totals(db, active_store, year, mon, last_day):
    """Sum (igst, cgst, sgst, taxable) of the sender-side transfer mirror bills
    for GSTR-3B Table 3.1(a). Sums the SAME header fields the receiver's ITC
    aggregation reads (igst/cgst/sgst_total, taxable_amount), so the sender's
    outward figure equals the receiver's ITC claim to the paisa. A read
    failure raises (via _transfer_outward_bills, M7)."""
    igst = cgst = sgst = taxable = 0.0
    for b in _transfer_outward_bills(db, active_store, year, mon, last_day):
        igst = round(igst + float(b.get("igst_total", 0) or 0), 2)
        cgst = round(cgst + float(b.get("cgst_total", 0) or 0), 2)
        sgst = round(sgst + float(b.get("sgst_total", 0) or 0), 2)
        taxable = round(taxable + float(b.get("taxable_amount", 0) or 0), 2)
    return igst, cgst, sgst, taxable


def _rcm_placed(db, active_store, year, mon, last_day):
    """The reverse-charge bills THE bill placement (gst_itc._placement) puts
    on this store's GSTIN's return, and that placement's GSTIN-bound part:
    (match, bound match), or None when the store cannot be read."""
    entity_id, store_gstin, shops = _itc_store_scope(db, active_store)
    match = {
        **_placement(shops, entity_id, store_gstin, year, mon, last_day, db),
        "reverse_charge": True,
    }
    return match, match


def _rcm_from_vendor_bills(db, active_store, year, mon, last_day):
    """NEW-GST-RCM: inward supplies LIABLE TO REVERSE CHARGE for the month
    (GSTR-3B Table 3.1(d)) -- vendor_bills flagged reverse_charge=True. On
    these the BUYER owes the GST (paid in cash, then claimed as ITC in Table 4).
    Placed by the SAME rule as that credit (gst_itc._placement: the GSTIN the
    bill was received on), so a Maharashtra bill's liability and its credit
    are on the Maharashtra return -- never the liability on the company's
    Jharkhand return with nothing to offset it. Returns (igst, cgst, sgst,
    taxable). A read failure raises (M7)."""
    if db is None:
        return 0.0, 0.0, 0.0, 0.0
    return _sum_heads(db, _rcm_placed(db, active_store, year, mon, last_day)[0])


def _rcm_gstin_from_vendor_bills(db, active_store, year, mon, last_day):
    """R1 for Table 3.1(d): the GSTIN-bound slice of _rcm_from_vendor_bills, so
    the Cross-Check counts it once per GSTIN and the company-wide remainder
    (legacy bills naming no GSTIN) once per company -- exactly as it counts
    the credit. Returns (igst, cgst, sgst, taxable); a read failure raises."""
    if db is None:
        return 0.0, 0.0, 0.0, 0.0
    return _sum_heads(db, _rcm_placed(db, active_store, year, mon, last_day)[1])


def _rcm_credit_from_vendor_bills(db, active_store, year, mon, last_day):
    """Table 4(A)(3): the part of Table 4's credit that is reverse-charge tax
    -- the reverse-charge bills Table 4 itself keeps (_itc_match: credit
    cleared by org_validation.itc_claimable at booking, never a bill whose
    credit was switched off). A slice of itcAvailable, never added to it.
    Returns (igst, cgst, sgst); a read failure raises."""
    if db is None:
        return 0.0, 0.0, 0.0
    entity_id, store_gstin, shops = _itc_store_scope(db, active_store)
    match = _itc_match(shops, entity_id, store_gstin, year, mon, last_day, db)
    return _sum_heads(db, {**match, "reverse_charge": True})[:3]


def _compute_gstr3b(month: str, active_store: str, shops=None, db=None) -> dict:
    """Compute the IMS GSTR-3B return for a month: THE return of
    `active_store`'s GSTIN (H2, owner + CA 2026-10-19 -- GSTR-3B is a
    per-GSTIN return). Its outward supplies are every shop's on that GSTIN
    (gst_itc._itc_store_scope: BV-PUN-01 + BV-ONLINE-01 file one return),
    its credit the GSTIN's. `shops` narrows the outward side to those shops
    (the Cross-Check of an accountant who sees one shop of a shared GSTIN,
    whose GSTIN-wide figures it withholds); omitted, the whole GSTIN. `db`:
    the handle the caller already reads (the Finance GST tab, the
    Cross-Check), so one view never reads two databases; else its own.

    Extracted from the `/gstr3b` endpoint so the JSON-report endpoint and
    the GSTN portal-export endpoint share the SAME aggregation. Reads
    MongoDB, returns a dict. Raises HTTPException(400) on a malformed
    `month`.

    Table 3.1 - Outward taxable supplies: derived from completed sales invoices.
    Table 4   - ITC available: derived from recorded purchase invoices
                (vendor_bills cgst/sgst/igst_total), placed on the store's GSTIN
                (gst_itc._itc_match).
                Returns zeros when no purchase data is present.
    Table 4(D) - ineligible ITC: GST paid on a bill that claims no credit.
    Table 4(B)(1) - section 17(5) blocked credit (the accountant's tick),
                and 17(5)(h) credit reversed on stock written off this month.
    Table 5   - exempt, nil, non-GST and composition inward supplies, with
                purchases from unregistered suppliers (gst_itc._no_credit_inward,
                owner 2026-10-13 G3).
    Table 6.1 - Payment of tax: net cash liability = output tax - ITC.
    Returns all-zero figures when no data exists for the period. A read
    failure RAISES (M7) -- the doors answer 503 -- never a zero figure.
    """
    try:
        year, mon = int(month[:4]), int(month[5:7])
        # last_day feeds the vendor-bill helpers, which filter on bill_date --
        # a CALENDAR date, deliberately kept in calendar (not instant) frame.
        _, last_day = monthrange(year, mon)
        # THE IST-month window (half-open), the same one _compute_gstr1 and
        # _credit_note_totals read, so GSTR-1 and GSTR-3B agree on the period.
        from_dt, to_dt = ist_month_window_utc(year, mon)
    except Exception:
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

    # Output tax accumulators
    out_igst = 0.0
    out_cgst = 0.0
    out_sgst = 0.0
    out_taxable = 0.0

    # ITC accumulators (from purchase GRNs)
    itc_igst = 0.0
    itc_cgst = 0.0
    itc_sgst = 0.0

    # G3: Table 5 (no-GST inward) and Table 4(D) (GST paid, not claimed).
    no_credit = _no_credit_inward(None, active_store, 0, 0, 0)

    # R1: the GSTIN-bound ITC slice, split out of the total above.
    t_itc_igst = 0.0
    t_itc_cgst = 0.0
    t_itc_sgst = 0.0

    # RCM (Table 3.1(d)) accumulators -- inward supplies liable to reverse charge.
    rcm_igst = 0.0
    rcm_cgst = 0.0
    rcm_sgst = 0.0
    rcm_taxable = 0.0
    # ...and its GSTIN-bound slice (R1), counted once per GSTIN by the Cross-Check.
    t_rcm = (0.0, 0.0, 0.0, 0.0)
    # Table 4(A)(3): the reverse-charge part of the ITC above.
    itc_rcm = (0.0, 0.0, 0.0)
    supplier_reversal = {"igst": 0.0, "cgst": 0.0, "sgst": 0.0}
    # Section 17(5)(h): credit taken back on stock written off this month.
    writeoff_reversal = {"igst": 0.0, "cgst": 0.0, "sgst": 0.0}
    writeoff_reclaim = {"igst": 0.0, "cgst": 0.0, "sgst": 0.0}

    # Credit-note totals + the per-head excess the zero-clamp would otherwise
    # swallow SILENTLY (filled below when a DB is present).
    cn_igst = cn_cgst = cn_sgst = cn_taxable = 0.0
    cn_carry = {
        "integratedTax": 0.0,
        "centralTax": 0.0,
        "stateTax": 0.0,
        "taxableValue": 0.0,
    }

    entity_id = None
    store_gstin = ""
    store_legal_name = ""
    store_state = ""
    filing = [active_store]

    db = db if db is not None else _get_raw_db()
    if db is not None:
        # The header/export must name the same resolved registration that
        # receives Table 4 credit, including the entity/state fallback.
        entity_id, store_gstin, gstin_shops = _itc_store_scope(db, active_store)
        filing = sorted(shops) if shops is not None else gstin_shops
        store_doc = db["stores"].find_one({"store_id": active_store})
        if store_doc:
            store_legal_name = str(
                store_doc.get("store_name") or store_doc.get("name", "") or ""
            )
            store_state = str(store_doc.get("state", "") or "")

        # --- Output tax from orders (Phase I-5 field-name fix).
        #     Orders stamp `taxable` (taxable value) and `tax`
        #     (total GST), via _compute_per_category_gst — there are
        #     no `taxable_amount` / `cgst_amount` etc. fields. We
        #     split tax by deriving intra/inter from store vs
        #     customer state, in the same loop as GSTR-1 above.
        orders_col = db["orders"]
        customers_col = db["customers"]

        cust_docs: dict = {
            str(cust.get("customer_id", "")): cust
            for cust in customers_col.find({}, CUSTOMER_SUPPLY_FIELDS)
        }
        # Each order's OWN shop (review round 2): the same GSTIN's return must
        # not change with which of its shops is asked ('Jharkhand' / 'JH' / a
        # blank online shop). A shop with no state or GSTIN of its own is in
        # its registration's state.
        shop_docs = {
            sd.get("store_id"): {**sd, "gstin": sd.get("gstin") or store_gstin}
            for sd in db["stores"].find(
                {"store_id": {"$in": filing}},
                {"_id": 0, "store_id": 1, "state_code": 1, "state": 1, "gstin": 1},
            )
        }

        for order in orders_col.find(
            {
                # Every shop this return files for (H2).
                "store_id": {"$in": filing},
                # THE GST-return order rule (gst_base), GSTR-1's too.
                # The GST-document view (PR #1264): this return also nets the
                # credit notes, so an invoice cancelled behind a whole-invoice
                # note stays filed and its note reverses it once.
                **real_sale_match(db, gst_documents=True),
                "created_at": {"$gte": from_dt, "$lt": to_dt},
            }
        ):
            # A routed online order held on the seller check is not an
            # outward supply of THIS GSTIN until Re-map re-routes it --
            # the one question GSTR-1 and Tally ask too, so the returns
            # of one GSTIN never disagree on it.
            if _order_held_off_returns(db, order):
                continue
            # Orders carry `tax_amount` + `grand_total`, NOT `taxable` /
            # `taxable_amount`; derive taxable = grand_total - tax_amount.
            taxable, tax = _order_taxable_and_tax(order)
            if taxable <= 0 and tax <= 0:
                continue
            out_taxable += taxable

            # The head the tax invoice PRINTED (_order_supply: the order's own
            # stamp, else the invoice's state-code rule), split per rate by
            # the invoice's split rule (_rate_heads) -- exactly GSTR-1's rows.
            is_inter_state = _order_supply(
                order,
                shop_docs.get(order.get("store_id")) or {"gstin": store_gstin},
                cust_docs.get(str(order.get("customer_id", ""))),
            )[0]
            for rl in _rate_heads(
                _order_rate_lines(order.get("items") or [], taxable, tax, order),
                is_inter_state,
            ):
                out_igst += rl["igst"]
                out_cgst += rl["cgst"]
                out_sgst += rl["sgst"]

        # NEW-GST-TRANSFER-OUTWARD (GAP A): the sender's deemed outward supply
        # on inter-GSTIN stock transfers -> Table 3.1(a). Reads the SAME mirror
        # vendor_bills docs the receiver claims ITC from, so sender outward tax
        # == receiver ITC claim by construction (paisa-exact).
        for shop in filing:
            t_igst, t_cgst, t_sgst, t_taxable = _transfer_outward_totals(
                db, shop, year, mon, last_day
            )
            out_igst += t_igst
            out_cgst += t_cgst
            out_sgst += t_sgst
            out_taxable += t_taxable

        # Table 3.1(a) is NET of credit notes - goods that came back are not an
        # outward supply. Clamped at zero: a month with more refunds than sales
        # is a carry-forward question for the accountant, never a negative
        # liability on this screen. But the clamp must never be SILENT -- it
        # once swallowed a credit note reversing CGST/SGST on a sale that was
        # filed under IGST, so the wrong-head error showed nowhere while the
        # entity paid IGST on refunded goods. Whatever each head cannot absorb
        # is reported in `creditNoteCarryForward` (and logged) for the
        # accountant to carry into the next period's return.
        # Every shop's credit notes, netted against the return's outward tax.
        for shop in filing:
            n_i, n_c, n_s, n_t = _credit_note_totals(db, shop, year, mon, last_day)
            cn_igst, cn_cgst = round(cn_igst + n_i, 2), round(cn_cgst + n_c, 2)
            cn_sgst, cn_taxable = round(cn_sgst + n_s, 2), round(cn_taxable + n_t, 2)
        cn_carry = {
            "integratedTax": round(max(0.0, cn_igst - out_igst), 2),
            "centralTax": round(max(0.0, cn_cgst - out_cgst), 2),
            "stateTax": round(max(0.0, cn_sgst - out_sgst), 2),
            "taxableValue": round(max(0.0, cn_taxable - out_taxable), 2),
        }
        if any(v > 0 for v in cn_carry.values()):
            logger.warning(
                "[GSTR-3B] %s %s: credit notes exceed outward tax on a head "
                "(carry-forward IGST %.2f / CGST %.2f / SGST %.2f); the excess "
                "is NOT netted on this screen - either a wrong-head credit "
                "note, or a refund-heavy month to carry forward.",
                active_store,
                month,
                cn_carry["integratedTax"],
                cn_carry["centralTax"],
                cn_carry["stateTax"],
            )
        out_igst = max(0.0, out_igst - cn_igst)
        out_cgst = max(0.0, out_cgst - cn_cgst)
        out_sgst = max(0.0, out_sgst - cn_sgst)
        out_taxable = max(0.0, out_taxable - cn_taxable)

        # BUG-138: ITC from recorded purchase invoices (vendor_bills), not the
        # qty-only `grns` collection that made ITC always 0 (-> GST over-paid).
        itc_igst, itc_cgst, itc_sgst = _itc_from_vendor_bills(
            db, active_store, year, mon, last_day
        )
        # R1: split the GSTIN-bound slice (every bill received on this GSTIN,
        # plus GSTIN-less transfer mirrors at any shop on that GSTIN) out of the total so
        # the cross-check aggregator can dedupe it once per GSTIN while the
        # company-wide remainder (legacy bills naming no GSTIN) is deduped once
        # per company. The slice is the SAME _itc_match AND-ed with the GSTIN
        # binding, so remainder = total - slice exactly.
        t_itc_igst, t_itc_cgst, t_itc_sgst = _itc_gstin_from_vendor_bills(
            db, active_store, year, mon, last_day
        )

        no_credit = _no_credit_inward(db, active_store, year, mon, last_day)

        # NEW-GST-RCM: Table 3.1(d) inward supplies liable to reverse charge.
        rcm_igst, rcm_cgst, rcm_sgst, rcm_taxable = _rcm_from_vendor_bills(
            db, active_store, year, mon, last_day
        )
        t_rcm = _rcm_gstin_from_vendor_bills(db, active_store, year, mon, last_day)
        itc_rcm = _rcm_credit_from_vendor_bills(db, active_store, year, mon, last_day)
        from ...services.supplier_credit_reads import confirmed_itc_heads
        try:
            if store_gstin:
                supplier_reversal = confirmed_itc_heads(
                    db, entity_id=entity_id, gstin=store_gstin, period=month)
        except Exception as exc:
            raise HTTPException(503, "Confirmed supplier credits are unavailable; please retry.") from exc
        itc_igst -= supplier_reversal["igst"]
        itc_cgst -= supplier_reversal["cgst"]
        itc_sgst -= supplier_reversal["sgst"]
        # Every confirmation retains a verified original GSTIN. Its reversal
        # belongs entirely to that registration's slice, including later months.
        t_itc_igst -= supplier_reversal["igst"]
        t_itc_cgst -= supplier_reversal["cgst"]
        t_itc_sgst -= supplier_reversal["sgst"]
        # Section 17(5)(h): the credit claimed on goods this GSTIN wrote off
        # (lost, stolen, destroyed) this month comes off its net credit and
        # shows in Table 4(B)(1) (services/writeoff_itc, every write-off door).
        # A piece back after its write-off month was filed re-claims that
        # credit this month (4(A)(5), never a negative 4(B)(1)).
        from ...services.writeoff_itc import month_reclaim, month_reversal

        writeoff_reversal = month_reversal(db, store_gstin, month)
        writeoff_reclaim = month_reclaim(db, store_gstin, month)
        itc_igst += writeoff_reclaim["igst"] - writeoff_reversal["igst"]
        itc_cgst += writeoff_reclaim["cgst"] - writeoff_reversal["cgst"]
        itc_sgst += writeoff_reclaim["sgst"] - writeoff_reversal["sgst"]
        t_itc_igst += writeoff_reclaim["igst"] - writeoff_reversal["igst"]
        t_itc_cgst += writeoff_reclaim["cgst"] - writeoff_reversal["cgst"]
        t_itc_sgst += writeoff_reclaim["sgst"] - writeoff_reversal["sgst"]

    # Net cash liability = output tax less the ITC the law lets set it off
    # (across heads: IGST credit also pays CGST/SGST), plus reverse-charge tax,
    # always discharged in CASH. One rule: net_cash_due.
    cash_igst, cash_cgst, cash_sgst = net_cash_due(
        (out_igst, out_cgst, out_sgst),
        (itc_igst, itc_cgst, itc_sgst),
        (rcm_igst, rcm_cgst, rcm_sgst),
    )

    def _r(v: float) -> float:
        return round(v, 2)

    unallocated = _unallocated_legacy_bills(db, entity_id, year, mon, last_day)
    from ...services.writeoff_itc import unallocated_writeoffs

    return {
        "period": month,
        "unallocatedLegacyBills": unallocated,
        # Write-off reversals (17(5)(h)) whose bill no GSTIN's return places
        # yet: held off every return, shown so the accountant settles the bill.
        "unallocatedWriteOffs": unallocated_writeoffs(db, entity_id, month) if db is not None else {
            "count": 0, "tax": 0.0, "bill_numbers": []},
        # The shops this return files for (H2): every shop on the GSTIN.
        "shops": filing,
        "gstin": store_gstin,
        "legalName": store_legal_name,
        "storeState": store_state,
        "outwardTaxableValue": _r(out_taxable),
        "outwardTaxableSupplies": {
            "integratedTax": _r(out_igst),
            "centralTax": _r(out_cgst),
            "stateTax": _r(out_sgst),
            "cess": 0.0,
        },
        # What Table 3.1(a) was netted BY (credit notes issued in the period),
        # and the per-head excess the zero-clamp could not absorb. A non-zero
        # carry-forward is the accountant's cue: either a wrong-head credit
        # note, or a refund-heavy month whose excess reversal must be carried
        # into the next period -- it is never silently discarded.
        "creditNotes": {
            "integratedTax": _r(cn_igst),
            "centralTax": _r(cn_cgst),
            "stateTax": _r(cn_sgst),
            "taxableValue": _r(cn_taxable),
        },
        "creditNoteCarryForward": cn_carry,
        # Table 3.1(d): inward supplies liable to reverse charge -- the buyer's
        # own GST liability on RCM purchases (NEW-GST-RCM).
        "inwardSuppliesReverseChargeValue": _r(rcm_taxable),
        "inwardSuppliesReverseCharge": {
            "integratedTax": _r(rcm_igst),
            "centralTax": _r(rcm_cgst),
            "stateTax": _r(rcm_sgst),
            "cess": 0.0,
        },
        # R1: the GSTIN-bound part of Table 3.1(d) (bills received on this
        # GSTIN); the rest is company-wide. Same split as itcAvailableGstin.
        "inwardSuppliesReverseChargeGstin": {
            "integratedTax": _r(t_rcm[0]),
            "centralTax": _r(t_rcm[1]),
            "stateTax": _r(t_rcm[2]),
            "taxableValue": _r(t_rcm[3]),
        },
        "zeroRatedValue": 0.0,
        "zeroRatedSupplies": {
            "integratedTax": 0.0,
            "centralTax": 0.0,
            "stateTax": 0.0,
            "cess": 0.0,
        },
        "itcAvailable": {
            "integratedTax": _r(itc_igst),
            "centralTax": _r(itc_cgst),
            "stateTax": _r(itc_sgst),
            "cess": 0.0,
        },
        "itcReversed": {
            "integratedTax": _r(supplier_reversal["igst"]),
            "centralTax": _r(supplier_reversal["cgst"]),
            "stateTax": _r(supplier_reversal["sgst"]),
            "cess": 0.0,
        },
        # Table 4(A)(3): the reverse-charge part of itcAvailable (inside it,
        # never on top); the portal upload files it apart from 4(A)(5).
        "itcReverseCharge": {
            "integratedTax": _r(itc_rcm[0]),
            "centralTax": _r(itc_rcm[1]),
            "stateTax": _r(itc_rcm[2]),
            "cess": 0.0,
        },
        # R1: itcAvailable split into the company-wide remainder (bills naming
        # no GSTIN) and the GSTIN-bound slice (remainder + slice ==
        # itcAvailable). The cross-check aggregator dedupes each at its true
        # scope, so a multi-GSTIN company's ITC is independent of store
        # enumeration order and no bill counts on two registrations.
        "itcAvailableRegular": {
            "integratedTax": _r(itc_igst - t_itc_igst),
            "centralTax": _r(itc_cgst - t_itc_cgst),
            "stateTax": _r(itc_sgst - t_itc_sgst),
            "cess": 0.0,
        },
        "itcAvailableGstin": {
            "integratedTax": _r(t_itc_igst),
            "centralTax": _r(t_itc_cgst),
            "stateTax": _r(t_itc_sgst),
            "cess": 0.0,
        },
        # Table 4(D): GST paid on purchase bills that claim no input credit.
        "itcIneligible": {
            "integratedTax": no_credit["igst"],
            "centralTax": no_credit["cgst"],
            "stateTax": no_credit["sgst"],
            "cess": 0.0,
        },
        # Table 4(B)(1): GST on bills ticked 'credit not claimable, section
        # 17(5)'. Never in itcAvailable (the net credit); the portal file
        # shows it in 4(A)(5) and reverses it here (gstn_export).
        "itcBlocked": {
            "integratedTax": no_credit["blocked_igst"],
            "centralTax": no_credit["blocked_cgst"],
            "stateTax": no_credit["blocked_sgst"],
            "cess": 0.0,
        },
        # Table 4(B)(1), section 17(5)(h): credit claimed earlier on stock
        # written off this month (services/writeoff_itc). Already taken off
        # itcAvailable; the portal file adds it back to 4(A)(5) and reverses
        # it here, so 4(C) is the net credit.
        "itcWriteOffReversed": {
            "integratedTax": _r(writeoff_reversal["igst"]),
            "centralTax": _r(writeoff_reversal["cgst"]),
            "stateTax": _r(writeoff_reversal["sgst"]),
            "cess": 0.0,
        },
        # Re-claimed this month on written-off pieces that came back after
        # their month was filed: already in itcAvailable, so 4(A)(5) carries
        # it (gstn_export); shown on its own, never netted into 4(B)(1).
        "itcWriteOffReclaimed": {
            "integratedTax": _r(writeoff_reclaim["igst"]),
            "centralTax": _r(writeoff_reclaim["cgst"]),
            "stateTax": _r(writeoff_reclaim["sgst"]),
            "cess": 0.0,
        },
        # Table 5: exempt, nil-rated, non-GST and composition inward supplies,
        # purchases from unregistered suppliers among them.
        "inwardExemptNilNonGst": {
            "interState": no_credit["inter"],
            "intraState": no_credit["intra"],
        },
        "exemptSupplies": 0.0,
        "taxPayable": {
            "integratedTax": _r(out_igst),
            "centralTax": _r(out_cgst),
            "stateTax": _r(out_sgst),
            "cess": 0.0,
        },
        # Table 6.1 "paid through ITC": the credit the cash rule actually used
        # -- the liability less what net_cash_due leaves for cash. Never the
        # whole credit: reverse-charge tax and unused credit are not set off.
        # A negative net credit adds repayment liability. Include it before
        # subtracting cash so positive credit from another head used against
        # that liability is counted, without inventing negative utilisation.
        "itcUtilized": {
            "integratedTax": _r(max(0, out_igst + max(0, -itc_igst) + rcm_igst - cash_igst)),
            "centralTax": _r(max(0, out_cgst + max(0, -itc_cgst) + rcm_cgst - cash_cgst)),
            "stateTax": _r(max(0, out_sgst + max(0, -itc_sgst) + rcm_sgst - cash_sgst)),
            "cess": 0.0,
        },
        "taxPaidCash": {
            "integratedTax": _r(cash_igst),
            "centralTax": _r(cash_cgst),
            "stateTax": _r(cash_sgst),
            "cess": 0.0,
        },
        "interest": {
            "integratedTax": 0.0,
            "centralTax": 0.0,
            "stateTax": 0.0,
            "cess": 0.0,
        },
        "lateFee": 0.0,
    }


def _gstin_return_shop(store_id: Optional[str], current_user: dict) -> str:
    """The shop whose GSTIN's GSTR-3B is asked for (_return_shop), once the
    caller is shown to see EVERY shop on that GSTIN (H2, owner + CA
    2026-10-19): the return files for all of them, so a reader who sees only
    some would read -- and could upload -- other shops' sales tax, or a
    return short of it. 403 in words otherwise; 503 when the shops cannot be
    read."""
    from ...dependencies import user_store_scope

    shop = _return_shop(store_id, current_user)
    db = _get_raw_db()
    if db is None:
        raise HTTPException(status_code=503, detail="GSTR-3B is unavailable; please retry.")
    try:
        filing = _itc_store_scope(db, shop)[2]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="GSTR-3B is unavailable; please retry.") from exc
    every, mine = user_store_scope(current_user)
    if not every and not all(s in mine for s in filing):
        raise HTTPException(
            status_code=403,
            detail=f"GSTR-3B covers {len(filing)} shops on this GSTIN - ask an admin.",
        )
    return shop


def _gstin_return(month: str, shop: str) -> dict:
    """THE GSTIN return for the doors: a read failure is a 503, never zeros."""
    try:
        return _compute_gstr3b(month, shop)
    except HTTPException:
        raise
    except UnfileableOrder as exc:  # a retry cannot fix it: name the order
        raise HTTPException(status_code=422, detail=f"GSTR-3B: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("[GSTR-3B] %s %s could not be read", shop, month)
        raise HTTPException(
            status_code=503, detail="GSTR-3B figures could not be read; please retry."
        ) from exc


@router.get("/gstr3b")
async def gstr3b_report(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """GSTR-3B summary return (IMS internal shape) of the shop's GSTIN -- every
    shop on it (H2). See _compute_gstr3b. The accounts roles' alone (R1,
    owner ruling 2026-10-07): it carries input tax from supplier bills; a
    manager is a 403. A non-admin login with no shop is refused (R4,
    _return_shop); one who cannot see every shop on the GSTIN too (H2)."""
    active_store = _gstin_return_shop(store_id, current_user)
    return strip_input_tax(_gstin_return(month, active_store), current_user)


@router.get("/gstr3b/gstn-json")
async def gstr3b_gstn_json(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    year: Optional[int] = Query(
        None, description="Optional year; combined with `month` as a number when given"
    ),
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(
        None, description="Reserved — entity-level rollup not yet wired; store_id wins"
    ),
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """GSTR-3B shaped for the GST portal's offline upload tool -- the return of
    the shop's GSTIN, every shop on it (H2).

    Reuses _compute_gstr3b (no duplicated aggregation) and runs the pure
    mapping in services/gstn_export.py.

    The accounts roles' alone (R5, owner ruling 2026-10-12): the upload IS the
    return, Table 4 input credit included, and to_gstr3b_json writes Table 4
    whatever it is handed -- an export without the credit would carry a zero
    ITC that files as "no credit claimed". The filer is accounts; a manager is
    refused rather than handed that document. So is an accountant login
    with no shop (R4, _return_shop): it holds no shop whose return to file;
    and one who cannot see every shop on the GSTIN (H2).
    """
    from ...services.gstn_export import to_gstr3b_json

    active_store = _gstin_return_shop(store_id, current_user)
    period = _normalise_period(month, year)
    data = _gstin_return(period, active_store)
    unallocated = data.get("unallocatedLegacyBills") or {}
    if unallocated.get("failed"):  # unread is not "unallocated" (review round 2)
        raise HTTPException(status_code=503, detail="GSTR-3B figures could not be read; please retry.")
    if unallocated.get("count"):
        raise HTTPException(
            status_code=409,
            detail="Assign each unallocated legacy purchase bill to its receiving GSTIN before downloading GSTN JSON. Open the GST cross-check and correct the bill allocation.",
        )
    return to_gstr3b_json(data, gstin=data.get("gstin", ""), period=period)
