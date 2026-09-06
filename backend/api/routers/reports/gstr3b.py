"""GSTR-3B (summary return): credit notes, RCM, computation, endpoints."""

from fastapi import Depends, Query
from typing import Optional
from datetime import date
from ...utils.ist import (
    ist_day_start_utc,
    ist_month_window_utc,
)
from calendar import monthrange
from ..auth import require_roles
from ...dependencies import (
    validate_store_access,
)
from ._shared import (
    _REPORT_FINANCE_ROLES,
    logger,
    router,
)
from .gst_base import (
    _get_raw_db,
    _normalise_period,
    _order_is_interstate,
    _order_taxable_and_tax,
)
from .gst_itc import (
    _cn_foreign_store,
    _itc_from_vendor_bills,
    _itc_transfer_from_vendor_bills,
    _ledger_row_return_doc,
    _return_interstate_flag,
    _transfer_outward_bills,
)

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
    worse than the bug being fixed. Fail-soft -> zeros.
    """
    igst = cgst = sgst = taxable = 0.0
    if db is None:
        return 0.0, 0.0, 0.0, 0.0
    try:
        from_dt, to_dt = ist_month_window_utc(year, mon)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0, 0.0, 0.0

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
    try:
        _all_ledger = db.get_collection("credit_note_ledger")
        if _all_ledger is not None:
            for _row in _all_ledger.find(
                {"store_id": active_store, "type": "ISSUED"}, {"ref": 1, "reason": 1}
            ):
                for _f in ("ref", "reason"):
                    for _tok in str(_row.get(_f) or "").replace(",", " ").split():
                        if _tok.startswith("RET-"):
                            seen_returns.add(_tok.strip(".:;"))
    except Exception:  # noqa: BLE001 -- dedup is best-effort, totals are not
        pass

    def _split(tax, is_inter):
        if is_inter:
            return round(tax, 2), 0.0, 0.0
        c = round(tax / 2, 2)
        return 0.0, c, round(tax - c, 2)

    # ONE head-resolver state for BOTH legs (parent-order stamp first, customer
    # state as the fallback -- _return_interstate_flag). The ledger leg used to
    # hardcode intra-state for any row without a bool `interstate` stamp,
    # reversing CGST/SGST on a sale that was filed -- and stays, in GSTR-1 --
    # under IGST: the entity kept paying IGST on goods that came back, and the
    # max(0, ...) clamp downstream swallowed the CGST/SGST over-reversal.
    order_inter: dict = {}
    store_state = ""
    try:
        st = db.get_collection("stores").find_one({"store_id": active_store}) or {}
        store_state = str(st.get("state") or "")
    except Exception:  # noqa: BLE001
        pass

    try:
        ledger = db.get_collection("credit_note_ledger")
    except Exception:  # noqa: BLE001
        ledger = None
    if ledger is not None:
        try:
            for row in ledger.find(
                {
                    "store_id": active_store,
                    "type": "ISSUED",
                    "created_at": {
                        "$gte": from_dt.isoformat(),
                        "$lt": to_dt.isoformat(),
                    },
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
                inter = row.get("interstate")
                if not isinstance(inter, bool):
                    inter = _return_interstate_flag(
                        db,
                        ret_doc or {"customer_id": row.get("customer_id")},
                        store_state,
                        order_inter,
                    )
                i, c, sg = _split(t, inter)
                igst += i
                cgst += c
                sgst += sg
                taxable += tv
        except Exception:  # noqa: BLE001
            pass

    # A refund must reverse the SAME head the sale was filed under. Deriving it
    # afresh from the customer's state would re-answer a question the parent
    # order already answered - and answer it differently for an online order,
    # whose buyer record is minted stateless. So prefer the parent order's own
    # `interstate` stamp, exactly as the CDNR rows and Table 3.1(a) do.
    # Caught by mutation testing: hardcoding returns as intra-state reversed
    # CGST/SGST on a sale that sat in IGST, leaving BOTH heads wrong.
    # (`order_inter` + `store_state` are shared with the ledger leg above.)
    try:
        returns_col = db.get_collection("returns")
    except Exception:  # noqa: BLE001
        returns_col = None
    if returns_col is not None:
        try:
            for ret in returns_col.find(
                {
                    "store_id": active_store,
                    "status": {"$in": ["COMPLETED", "completed"]},
                }
            ):
                rid = str(ret.get("return_id") or "")
                if rid and rid in seen_returns:
                    continue
                when = ret.get("created_at")
                try:
                    if isinstance(when, str):
                        if not (from_dt.isoformat() <= when < to_dt.isoformat()):
                            continue
                    elif when is not None:
                        if not (from_dt <= when < to_dt):
                            continue
                    else:
                        continue
                except (TypeError, ValueError):
                    continue
                gb = ret.get("gst_breakup") or {}
                if not isinstance(gb, dict):
                    continue
                t = round(float(gb.get("tax") or 0.0), 2)
                tv = round(float(gb.get("taxable") or 0.0), 2)
                if t <= 0 and tv <= 0:
                    continue
                i, c, sg = _split(
                    t, _return_interstate_flag(db, ret, store_state, order_inter)
                )
                igst += i
                cgst += c
                sgst += sg
                taxable += tv
        except Exception:  # noqa: BLE001
            pass

    return round(igst, 2), round(cgst, 2), round(sgst, 2), round(taxable, 2)


def _transfer_outward_totals(db, active_store, year, mon, last_day):
    """Sum (igst, cgst, sgst, taxable) of the sender-side transfer mirror bills
    for GSTR-3B Table 3.1(a). Sums the SAME header fields the receiver's ITC
    aggregation reads (igst/cgst/sgst_total, taxable_amount), so the sender's
    outward figure equals the receiver's ITC claim to the paisa. Fail-soft ->
    zeros (via _transfer_outward_bills)."""
    igst = cgst = sgst = taxable = 0.0
    for b in _transfer_outward_bills(db, active_store, year, mon, last_day):
        igst = round(igst + float(b.get("igst_total", 0) or 0), 2)
        cgst = round(cgst + float(b.get("cgst_total", 0) or 0), 2)
        sgst = round(sgst + float(b.get("sgst_total", 0) or 0), 2)
        taxable = round(taxable + float(b.get("taxable_amount", 0) or 0), 2)
    return igst, cgst, sgst, taxable


def _rcm_from_vendor_bills(db, active_store, year, mon, last_day):
    """NEW-GST-RCM: inward supplies LIABLE TO REVERSE CHARGE for the month
    (GSTR-3B Table 3.1(d)) -- vendor_bills flagged reverse_charge=True, scoped to
    the store's entity. On these the BUYER owes the GST (paid in cash, then
    claimed as ITC separately). Returns (igst, cgst, sgst, taxable). Mirrors
    _itc_from_vendor_bills' entity + string-date-window match. Fail-soft -> zeros."""
    if db is None:
        return 0.0, 0.0, 0.0, 0.0
    try:
        entity_id = None
        try:
            _srow = db["stores"].find_one({"store_id": active_store}, {"entity_id": 1})
            entity_id = (_srow or {}).get("entity_id")
        except Exception:
            entity_id = None
        month_lo = f"{year:04d}-{mon:02d}-01"
        month_hi = f"{year:04d}-{mon:02d}-{last_day:02d}T23:59:59"
        vb_match: dict = {
            "reverse_charge": True,
            "status": {"$nin": ["CANCELLED", "cancelled", "VOID", "voided"]},
            "$or": [
                {"invoice_date": {"$gte": month_lo, "$lte": month_hi}},
                {"bill_date": {"$gte": month_lo, "$lte": month_hi}},
            ],
        }
        if entity_id:
            vb_match["recipient_entity_id"] = entity_id
        pipeline = [
            {"$match": vb_match},
            {
                "$group": {
                    "_id": None,
                    "igst": {"$sum": "$igst_total"},
                    "cgst": {"$sum": "$cgst_total"},
                    "sgst": {"$sum": "$sgst_total"},
                    "taxable": {"$sum": "$taxable_amount"},
                }
            },
        ]
        res = list(db["vendor_bills"].aggregate(pipeline))
        if res:
            a = res[0]
            return (
                float(a.get("igst", 0.0) or 0.0),
                float(a.get("cgst", 0.0) or 0.0),
                float(a.get("sgst", 0.0) or 0.0),
                float(a.get("taxable", 0.0) or 0.0),
            )
    except Exception:
        pass
    return 0.0, 0.0, 0.0, 0.0


def _compute_gstr3b(month: str, active_store: str) -> dict:
    """Compute the IMS GSTR-3B report dict for a (month, store).

    Extracted from the `/gstr3b` endpoint so the JSON-report endpoint and
    the GSTN portal-export endpoint share the SAME aggregation. Reads
    MongoDB, returns a dict. Raises HTTPException(400) on a malformed
    `month`.

    Table 3.1 - Outward taxable supplies: derived from completed sales invoices.
    Table 4   - ITC available: derived from recorded purchase invoices
                (vendor_bills cgst/sgst/igst_total), scoped to the store's entity.
                Returns zeros when no purchase data is present.
    Table 6.1 - Payment of tax: net cash liability = output tax - ITC.
    Returns all-zero figures when no data exists for the period.
    """
    try:
        year, mon = int(month[:4]), int(month[5:7])
        # last_day feeds the vendor-bill helpers, which filter on bill_date --
        # a CALENDAR date, deliberately kept in calendar (not instant) frame.
        _, last_day = monthrange(year, mon)
        # Same IST-month -> naive-UTC created_at window as _compute_gstr1, so
        # GSTR-1 and GSTR-3B agree on which invoices belong to the period.
        from_dt = ist_day_start_utc(date(year, mon, 1))
        nxt_y, nxt_m = (year + 1, 1) if mon == 12 else (year, mon + 1)
        to_dt = ist_day_start_utc(date(nxt_y, nxt_m, 1))
    except Exception:
        from fastapi import HTTPException

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

    # R1: transfer-borne (GSTIN-scoped) ITC slice, split out of the total above.
    t_itc_igst = 0.0
    t_itc_cgst = 0.0
    t_itc_sgst = 0.0

    # RCM (Table 3.1(d)) accumulators -- inward supplies liable to reverse charge.
    rcm_igst = 0.0
    rcm_cgst = 0.0
    rcm_sgst = 0.0
    rcm_taxable = 0.0

    # Credit-note totals + the per-head excess the zero-clamp would otherwise
    # swallow SILENTLY (filled below when a DB is present).
    cn_igst = cn_cgst = cn_sgst = cn_taxable = 0.0
    cn_carry = {
        "integratedTax": 0.0,
        "centralTax": 0.0,
        "stateTax": 0.0,
        "taxableValue": 0.0,
    }

    store_gstin = ""
    store_legal_name = ""
    store_state = ""

    db = _get_raw_db()
    if db is not None:
        try:
            stores_col = db["stores"]
            store_doc = stores_col.find_one({"store_id": active_store})
            if store_doc:
                store_gstin = str(store_doc.get("gstin", "") or "")
                store_legal_name = str(
                    store_doc.get("store_name") or store_doc.get("name", "") or ""
                )
                store_state = str(store_doc.get("state", "") or "")
        except Exception:
            pass

        try:
            # --- Output tax from orders (Phase I-5 field-name fix).
            #     Orders stamp `taxable` (taxable value) and `tax`
            #     (total GST), via _compute_per_category_gst — there are
            #     no `taxable_amount` / `cgst_amount` etc. fields. We
            #     split tax by deriving intra/inter from store vs
            #     customer state, in the same loop as GSTR-1 above.
            orders_col = db["orders"]
            customers_col = db["customers"]

            cust_state_map: dict = {}
            try:
                for cust in customers_col.find({}, {"customer_id": 1, "state": 1}):
                    cust_state_map[str(cust.get("customer_id", ""))] = str(
                        cust.get("state", "") or ""
                    )
            except Exception:
                pass

            for order in orders_col.find(
                {
                    "store_id": active_store,
                    "status": {"$nin": ["CANCELLED", "DRAFT", "cancelled", "draft", "HISTORICAL", "historical"]},
                    "created_at": {"$gte": from_dt, "$lt": to_dt},
                }
            ):
                # Orders carry `tax_amount` + `grand_total`, NOT `taxable` /
                # `taxable_amount`; derive taxable = grand_total - tax_amount.
                taxable, tax = _order_taxable_and_tax(order)
                if taxable <= 0 and tax <= 0:
                    continue
                out_taxable += taxable

                cust_id = str(order.get("customer_id", ""))
                customer_state = cust_state_map.get(cust_id, "") or store_state
                # OS-008: the order's own interstate flag wins (online orders
                # carry it), keeping 3.1(a) heads consistent with GSTR-1 and
                # the cross-check; state comparison stays the fallback.
                is_inter_state = _order_is_interstate(
                    order, store_state, customer_state
                )
                if is_inter_state:
                    out_igst += tax
                else:
                    out_cgst += tax / 2
                    out_sgst += tax / 2
        except Exception:
            pass

        # NEW-GST-TRANSFER-OUTWARD (GAP A): the sender's deemed outward supply
        # on inter-GSTIN stock transfers -> Table 3.1(a). Reads the SAME mirror
        # vendor_bills docs the receiver claims ITC from, so sender outward tax
        # == receiver ITC claim by construction (paisa-exact).
        t_igst, t_cgst, t_sgst, t_taxable = _transfer_outward_totals(
            db, active_store, year, mon, last_day
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
        cn_igst, cn_cgst, cn_sgst, cn_taxable = _credit_note_totals(
            db, active_store, year, mon, last_day
        )
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
        # R1: split the transfer-borne (GSTIN-scoped) slice out of the total so
        # the cross-check aggregator can dedupe it once per GSTIN while the
        # regular (entity-scoped) remainder is deduped once per entity. The
        # transfer slice uses the SAME filters restricted to source_transfer_id
        # bills, so regular = total - transfer exactly.
        t_itc_igst, t_itc_cgst, t_itc_sgst = _itc_transfer_from_vendor_bills(
            db, active_store, year, mon, last_day
        )

        # NEW-GST-RCM: Table 3.1(d) inward supplies liable to reverse charge.
        rcm_igst, rcm_cgst, rcm_sgst, rcm_taxable = _rcm_from_vendor_bills(
            db, active_store, year, mon, last_day
        )

    # Net cash liability = (output tax - ITC) + reverse-charge tax. RCM is always
    # discharged in CASH (it cannot be set off against ITC), so it adds on top of
    # the output-minus-ITC cash. When there are no RCM bills these terms are 0.
    cash_igst = max(0.0, out_igst - itc_igst) + rcm_igst
    cash_cgst = max(0.0, out_cgst - itc_cgst) + rcm_cgst
    cash_sgst = max(0.0, out_sgst - itc_sgst) + rcm_sgst

    def _r(v: float) -> float:
        return round(v, 2)

    return {
        "period": month,
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
        # R1: itcAvailable split into the entity-scoped regular remainder and the
        # GSTIN-scoped transfer slice (regular + transfer == itcAvailable). The
        # cross-check aggregator dedupes each at its true scope so a multi-GSTIN
        # entity's ITC is independent of store enumeration order.
        "itcAvailableRegular": {
            "integratedTax": _r(itc_igst - t_itc_igst),
            "centralTax": _r(itc_cgst - t_itc_cgst),
            "stateTax": _r(itc_sgst - t_itc_sgst),
            "cess": 0.0,
        },
        "itcAvailableTransfer": {
            "integratedTax": _r(t_itc_igst),
            "centralTax": _r(t_itc_cgst),
            "stateTax": _r(t_itc_sgst),
            "cess": 0.0,
        },
        "exemptSupplies": 0.0,
        "taxPayable": {
            "integratedTax": _r(out_igst),
            "centralTax": _r(out_cgst),
            "stateTax": _r(out_sgst),
            "cess": 0.0,
        },
        "itcUtilized": {
            "integratedTax": _r(itc_igst),
            "centralTax": _r(itc_cgst),
            "stateTax": _r(itc_sgst),
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


@router.get("/gstr3b")
async def gstr3b_report(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GSTR-3B summary return (IMS internal shape). See _compute_gstr3b."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    return _compute_gstr3b(month, active_store)


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
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GSTR-3B shaped for the GST portal's offline upload tool.

    Reuses _compute_gstr3b (no duplicated aggregation) and runs the pure
    mapping in services/gstn_export.py.
    """
    from ...services.gstn_export import to_gstr3b_json

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    period = _normalise_period(month, year)
    data = _compute_gstr3b(period, active_store)
    try:
        return to_gstr3b_json(data, gstin=data.get("gstin", ""), period=period)
    except Exception:
        return to_gstr3b_json({}, gstin="", period=period)


