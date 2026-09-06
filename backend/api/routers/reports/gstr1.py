"""GSTR-1 (outward supplies): computation plus the two endpoints."""

from fastapi import Depends, Query
from typing import Optional
from datetime import date
from ...utils.ist import (
    ist_date_str,
    ist_day_start_utc,
)
from calendar import monthrange
from ..auth import require_roles
from ...dependencies import (
    validate_store_access,
)
from ._shared import (
    _REPORT_FINANCE_ROLES,
    _cdnr_note_number,
    _credit_note_date_ist,
    _gstr1_bill_number,
    router,
)
from .gst_base import (
    _b2cs_rate_lines,
    _get_raw_db,
    _normalise_period,
    _order_is_interstate,
    _order_taxable_and_tax,
)
from .gst_itc import (
    _cn_bucket_rate,
    _cn_foreign_store,
    _ledger_row_return_doc,
    _return_interstate_flag,
    _transfer_b2b_rows,
    _transfer_outward_bills,
)

def _compute_gstr1(month: str, active_store: str) -> dict:
    """Compute the IMS GSTR-1 report dict for a (month, store).

    Extracted from the `/gstr1` endpoint so both the JSON-report endpoint
    and the GSTN portal-export endpoint can share the SAME aggregation
    (no duplicated query logic). Pure-ish: reads MongoDB, returns a dict.
    Raises HTTPException(400) only on a malformed `month`.

    Classifies invoices into:
      - B2B  : orders where the customer has a GSTIN on file
      - B2CL : orders to consumers with invoice value > 250000
      - B2CS : consolidated summary of remaining consumer invoices

    Field-name fixes:
      - Reads `grand_total` (not legacy `total_amount`) and `tax_amount`
        (total GST) off the order; there is NO top-level `taxable` /
        `taxable_amount` field, so the taxable value is derived as
        grand_total - tax_amount (with a per-line `taxable_value` fallback)
        via _order_taxable_and_tax. orders._compute_per_category_gst
        guarantees taxable + tax == grand_total in inclusive AND exclusive
        modes, so this is exact.
      - Stores carry their own `state` + `gstin` in the `stores`
        collection — used to derive intra-state (CGST+SGST) vs
        inter-state (IGST) splits and to fill the GSTIN/legalName
        header. When the store row is absent, fallback is single-state
        chain assumption (all sales intra-state, tax split 50/50).

    Returns empty lists/summaries when no data exists for the period.
    Validation report (`validation`) flags B2B invoices missing GSTIN
    so the CA can fix them before downloading.
    """
    # Parse month to date range. The GST tax period is an IST calendar month,
    # but `created_at` is stored as a naive-UTC instant (BaseRepository) -- so
    # the month boundaries must be shifted through ist_day_start_utc. With the
    # old UTC-frame window an invoice minted 01-Jun 02:00 IST (= 31-May 20:30
    # UTC) filed into MAY's GSTR-1, contradicting its IST-minted Rule 46(b)
    # serial; on 1-Apr it even fell into the prior FY. The half-open
    # [from_dt, to_dt) window also closes the old `$lte 23:59:59` 1-second hole.
    try:
        year, mon = int(month[:4]), int(month[5:7])
        from_dt = ist_day_start_utc(date(year, mon, 1))
        nxt_y, nxt_m = (year + 1, 1) if mon == 12 else (year, mon + 1)
        to_dt = ist_day_start_utc(date(nxt_y, nxt_m, 1))
    except Exception:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

    b2b: list = []
    b2cl: list = []
    b2cs_map: dict = {}
    validation_issues: list = []
    # NEW-GST-TRANSFER-OUTWARD: per-line HSN detail of the sender-side transfer
    # deemed-supply bills (filled below; merged into the HSN summary instead of
    # the row-level dominant HSN so mixed 5%/18% transfers stay exact).
    transfer_hsn_lines: list = []

    # Store header (gstin + legalName + home state for intra/inter split)
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
            orders_col = db["orders"]
            customers_col = db["customers"]

            # Build a lookup: customer_id -> {gstin, name, state}
            cust_map: dict = {}
            try:
                for cust in customers_col.find(
                    {},
                    {"customer_id": 1, "gstin": 1, "name": 1, "state": 1},
                ):
                    cust_map[str(cust.get("customer_id", ""))] = {
                        "gstin": str(cust.get("gstin", "") or ""),
                        "name": str(cust.get("name", "") or ""),
                        "state": str(cust.get("state", "") or ""),
                    }
            except Exception:
                pass

            # Fetch completed orders in the date range. Filter uses real
            # datetime objects to match BaseRepository which writes
            # `created_at` as a Date — comparing a Date to an ISO string
            # silently never matches (this was the original GSTR bug).
            query = {
                "store_id": active_store,
                "status": {"$nin": ["CANCELLED", "DRAFT", "cancelled", "draft", "HISTORICAL", "historical"]},
                "created_at": {"$gte": from_dt, "$lt": to_dt},
            }

            for order in orders_col.find(query):
                cust_id = str(order.get("customer_id", ""))
                cust_info = cust_map.get(cust_id, {})
                customer_gstin = cust_info.get("gstin", "")
                customer_state = cust_info.get("state", "") or store_state
                customer_name = (
                    cust_info.get("name", "")
                    or order.get("customer_name", "")
                    or "Walk-in Customer"
                )

                # PRIMARY field map. Orders persist `grand_total` (what the
                # customer pays) + `tax_amount` (total GST); there is NO
                # top-level `taxable` / `taxable_amount` field, so the taxable
                # value is derived as grand_total - tax_amount (with a per-line
                # taxable_value fallback). See _order_taxable_and_tax.
                invoice_value = float(
                    order.get("grand_total", order.get("total_amount", 0)) or 0
                )
                taxable_value, total_tax = _order_taxable_and_tax(order)

                # Intra vs inter-state split. Online (Shopify) orders DO carry
                # their own `interstate` flag (stamped at ingest from the
                # delivery address) -- prefer it (OS-008); otherwise derive
                # from store_state vs customer_state. When customer_state is
                # empty (walk-in without state on file), assume same as store
                # (intra).
                is_inter_state = _order_is_interstate(
                    order, store_state, customer_state
                )
                if is_inter_state:
                    igst = round(total_tax, 2)
                    cgst = 0.0
                    sgst = 0.0
                else:
                    cgst = round(total_tax / 2, 2)
                    sgst = round(total_tax / 2, 2)
                    igst = 0.0

                bill_number = _gstr1_bill_number(order, validation_issues)
                created_raw = order.get("created_at", "")
                # BUG-104, VALUE rule (move the derived day FORWARD). This is
                # the invoiceDate on a FILED GSTR-1 row. The month window above
                # is already IST (ist_day_start_utc, correctly a BOUND moving
                # BACKWARD), so an order minted 01-Jun 02:00 IST is selected
                # into JUNE -- but str(created_at)[:10] printed 31-May on it, a
                # date OUTSIDE the tax period it is filed in. On 1 April it
                # printed a PRIOR-FINANCIAL-YEAR date against an IST-minted
                # Rule 46(b) serial. Same class as the creditNoteDate fixed in
                # _credit_note_date_ist; ist_date_str leaves a legacy ISO
                # STRING created_at untouched by design (unknown frame), which
                # is exactly the old behaviour for the migrated orders.
                invoice_date = ist_date_str(created_raw) if created_raw else month + "-01"
                place_of_supply = customer_state or store_state or "Unknown"

                # HSN: pull from the first line item if available; fallback
                # to 9004 (frames/lenses default per CBIC). GSTR-1 row-level
                # HSN is acceptable; section 12 HSN summary is computed
                # separately below.
                hsn_code = "9004"
                gst_rate_dominant = 5
                items = order.get("items") or []
                if items:
                    first = items[0] if isinstance(items[0], dict) else {}
                    if first.get("hsn_code"):
                        hsn_code = str(first.get("hsn_code"))
                    if first.get("gst_rate") is not None:
                        try:
                            gst_rate_dominant = int(first.get("gst_rate"))
                        except Exception:
                            pass

                base_invoice = {
                    "invoiceNumber": bill_number,
                    "invoiceDate": invoice_date,
                    "customerName": customer_name,
                    "placeOfSupply": place_of_supply,
                    "invoiceValue": round(invoice_value, 2),
                    "taxableValue": round(taxable_value, 2),
                    "cgst": cgst,
                    "sgst": sgst,
                    "igst": igst,
                    "totalTax": round(total_tax, 2),
                    "hsnCode": hsn_code,
                    "gstRate": gst_rate_dominant,
                }

                if customer_gstin:
                    # B2B: registered business with GSTIN
                    b2b.append(
                        {
                            **base_invoice,
                            "customerGSTIN": customer_gstin,
                            "customerState": customer_state or store_state,
                        }
                    )
                elif invoice_value > 250000:
                    # B2CL: large consumer invoice (> ₹2.5L)
                    b2cl.append(
                        {
                            **base_invoice,
                            "customerState": customer_state or store_state,
                        }
                    )
                    # An "out-of-state" B2CL with no customer_state is
                    # technically required to have one — flag it.
                    if invoice_value > 250000 and not customer_state:
                        validation_issues.append(
                            {
                                "level": "warn",
                                "invoice": bill_number,
                                "issue": "B2CL invoice missing customer state",
                            }
                        )
                else:
                    # B2CS: consolidate by (place_of_supply, gst_rate). NEW-GST-B2CS-HSN:
                    # an invoice can mix GST rates (e.g. a 5% frame + an 18%
                    # sunglass); split each line into the correct rate bucket
                    # instead of lumping the whole invoice under the first line's
                    # rate. Tax is split CGST/SGST (intra) or IGST (inter) per line.
                    for rate, line_taxable, line_tax in _b2cs_rate_lines(
                        items, taxable_value, total_tax
                    ):
                        key = f"{place_of_supply}|{rate}"
                        if key not in b2cs_map:
                            b2cs_map[key] = {
                                "placeOfSupply": place_of_supply,
                                "gstRate": rate,
                                "taxableValue": 0.0,
                                "cgst": 0.0,
                                "sgst": 0.0,
                                "igst": 0.0,
                                "totalTax": 0.0,
                            }
                        b2cs_map[key]["taxableValue"] += line_taxable
                        b2cs_map[key]["totalTax"] += line_tax
                        if is_inter_state:
                            b2cs_map[key]["igst"] += line_tax
                        else:
                            b2cs_map[key]["cgst"] += round(line_tax / 2, 2)
                            b2cs_map[key]["sgst"] += round(line_tax / 2, 2)

                # Validation: B2B without GSTIN — caught by the absence
                # of customer_gstin above. Add an explicit warning for
                # high-value invoices missing it.
                if invoice_value > 250000 and not customer_gstin:
                    validation_issues.append(
                        {
                            "level": "info",
                            "invoice": bill_number,
                            "issue": "Invoice > ₹2.5L without customer GSTIN — confirm B2C status",
                        }
                    )

        except Exception:
            pass

        # NEW-GST-TRANSFER-OUTWARD (GAP A): the sender side of inter-GSTIN
        # stock transfers. A Schedule I deemed supply to a distinct person (a
        # sister entity, or our own GSTIN in another state) is an OUTWARD B2B
        # supply of the SENDING GSTIN -- previously it flowed only into the
        # receiver's ITC (via the transfer mirror vendor_bill) and the sender
        # under-reported outward IGST. The mirror bill itself is the sender's
        # tax invoice, so its rows come from vendor_bills keyed by
        # from_store_id; the recipient GSTIN is our sister GSTIN. Rows are
        # flagged deemedSupply=True; the HSN summary uses their PER-LINE detail
        # (merged below) so a mixed 5%/18% transfer stays rate-exact.
        try:
            _t_bills = _transfer_outward_bills(
                db, active_store, year, mon, monthrange(year, mon)[1]
            )
            _t_rows, transfer_hsn_lines = _transfer_b2b_rows(_t_bills)
            for _t_row in _t_rows:
                if not _t_row.get("customerGSTIN"):
                    validation_issues.append(
                        {
                            "level": "warn",
                            "invoice": _t_row.get("invoiceNumber"),
                            "issue": (
                                "Transfer deemed-supply invoice missing recipient "
                                "GSTIN — it will be dropped from the portal B2B upload"
                            ),
                        }
                    )
            b2b.extend(_t_rows)
        except Exception:
            transfer_hsn_lines = []

    b2cs = [
        {
            **v,
            "taxableValue": round(v["taxableValue"], 2),
            "cgst": round(v["cgst"], 2),
            "sgst": round(v["sgst"], 2),
            "igst": round(v["igst"], 2),
            "totalTax": round(v["totalTax"], 2),
        }
        for v in b2cs_map.values()
    ]

    # CDNR (Credit/Debit Notes Register) and HSN summary
    cdnr: list = []
    hsn_by_rate: dict = {}
    # Per-report order->interstate cache, shared by the ledger pass and the
    # in-store returns pass so one refund can never resolve two heads.
    _cdnr_inter_cache: dict = {}

    if db is not None:
        # Process credit notes from returns/refunds in credit_note_ledger
        try:
            ledger_col = db.get_collection("credit_note_ledger") or db["credit_note_ledger"]
            if ledger_col is not None:
                # Query credit notes issued in the period. The ledger writes
                # created_at as a naive-UTC ISO STRING (store_credit_ledger.
                # make_entry), so the bounds stay strings -- the isoformat of
                # the IST-month window above is frame-consistent with them.
                ledger_query = {
                    "store_id": active_store,
                    "type": "ISSUED",
                    "created_at": {"$gte": from_dt.isoformat(), "$lt": to_dt.isoformat()},
                }
                # Allow for date stored as datetime or ISO string
                try:
                    for entry in ledger_col.find(ledger_query):
                        if not isinstance(entry, dict):
                            continue
                        # Credit notes reduce GST liability
                        cust_id = str(entry.get("customer_id", ""))
                        cust_info = cust_map.get(cust_id, {})
                        cust_gstin = cust_info.get("gstin", "")
                        cust_state = cust_info.get("state", "") or store_state

                        # Tax + taxable base for the credit note. PREFER the
                        # explicit GST split stamped on the ledger row (Shopify
                        # refund + in-store CREDIT_NOTE now stamp taxable/tax --
                        # the REAL output-tax reversal). Only FALL BACK to the
                        # gross-minus-net derivation for legacy rows that predate
                        # the stamp (where net = gross - tax). Without this a
                        # fee-less credit note (gross == net) reported tax 0.
                        gross_refund = float(entry.get("gross_refund") or 0.0)
                        net_refund = float(entry.get("net_refund", entry.get("amount", 0)) or 0.0)
                        explicit_tax = entry.get("tax")
                        explicit_taxable = entry.get("taxable")
                        if explicit_tax is not None:
                            cn_tax = round(float(explicit_tax or 0.0), 2)
                        else:
                            cn_tax = round(gross_refund - net_refund, 2) if gross_refund > 0 else 0.0
                        if explicit_taxable is not None:
                            cn_taxable = round(float(explicit_taxable or 0.0), 2)
                        else:
                            cn_taxable = round(net_refund, 2)

                        # Legacy rows booked under the CASHIER's store (not
                        # the order's) belong to the sale store's GSTR-1 --
                        # its in-store-returns pass reports them there.
                        # Reporting them here too filed one credit note under
                        # two GSTINs.
                        _ret_doc = _ledger_row_return_doc(db, entry)
                        if _cn_foreign_store(_ret_doc, active_store):
                            continue

                        # Split CGST/SGST vs IGST: prefer the head stamped from
                        # the PARENT order at booking time (`interstate`, bool
                        # -- online refunds carry it so the credit note reverses
                        # under the same head the sale filed under, OS-008 CDNR
                        # leg); for legacy rows without the stamp resolve via
                        # the SAME _return_interstate_flag the GSTR-3B leg and
                        # the in-store pass use (parent-order stamp first,
                        # customer state as the last fallback) so the two
                        # returns can never reverse different heads for one
                        # refund.
                        entry_interstate = entry.get("interstate")
                        if isinstance(entry_interstate, bool):
                            is_inter = entry_interstate
                        else:
                            is_inter = _return_interstate_flag(
                                db,
                                _ret_doc or {"customer_id": cust_id},
                                store_state,
                                _cdnr_inter_cache,
                                fallback_state=cust_info.get("state", "") or "",
                            )
                        if is_inter:
                            cn_igst = round(cn_tax, 2)
                            cn_cgst = 0.0
                            cn_sgst = 0.0
                        else:
                            cn_cgst = round(cn_tax / 2, 2)
                            cn_sgst = round(cn_tax / 2, 2)
                            cn_igst = 0.0

                        # GSTN-legal note number (<=16 chars): prefer the
                        # dedicated note_number stamped on synthesized
                        # historical CNs, else the internal ref capped at 16.
                        ref = _cdnr_note_number(entry)
                        cn_date = _credit_note_date_ist(
                            entry.get("created_at")
                        ) or (month + "-01")

                        # Credit notes carry the items' HSN/rate. Prefer the
                        # rate stamped on the ledger row (Shopify refund +
                        # in-store credit note stamp gst_rate); a legacy row
                        # without one derives it from its own tax/taxable --
                        # NEVER a fabricated 18% that raided the wrong HSN
                        # bucket.
                        cn_hsn = "9004"
                        cn_rate = _cn_bucket_rate(
                            entry.get("gst_rate"), cn_tax, cn_taxable
                        )
                        cn_place = cust_state or store_state or "Unknown"

                        cn_entry = {
                            "refReference": ref,
                            "creditNoteDate": cn_date,
                            "customerId": cust_id,
                            "customerName": cust_info.get("name", ""),
                            "customerGSTIN": cust_gstin,
                            "customerState": cn_place,
                            "placeOfSupply": cn_place,
                            "grossValue": round(gross_refund, 2),
                            "taxableValue": cn_taxable,
                            "cgst": cn_cgst,
                            "sgst": cn_sgst,
                            "igst": cn_igst,
                            "taxValue": round(cn_tax, 2),
                            "hsnCode": cn_hsn,
                            "gstRate": cn_rate,
                        }
                        cdnr.append(cn_entry)
                except Exception:
                    pass
        except Exception:
            pass

        # ------------------------------------------------------------------
        # IN-STORE RETURNS as credit notes. THE REFUND ITSELF is the taxable
        # event: goods came back, so the output tax on them reverses, and HOW
        # the money went back is irrelevant to GST.
        #
        # This block exists because the CDNR source above is the STORE-CREDIT
        # ledger, and a cash / UPI / card refund correctly writes NO store
        # credit -- the customer got money, not a promise of money. So the tax
        # reversal existed nowhere: measured on production, ALL 20 completed
        # returns had no credit note and Rs 3,209.96 of output tax was still
        # being declared on goods that had come back.
        #
        # The fix is NOT to mint store credit for a cash refund (that would
        # hand the customer spendable money they never earned, on top of their
        # cash). It is to read the tax event from where it is already recorded:
        # `returns.gst_breakup`, stamped by the return door itself. No new tax
        # arithmetic is introduced here.
        try:
            returns_col = db.get_collection("returns")
            if returns_col is not None:
                # A return that DID mint store credit is already in `cdnr` from
                # the ledger pass; counting it twice would over-reverse the tax,
                # which under-declares - worse than the bug being fixed.
                # (_cdnr_inter_cache is shared with the ledger pass above.)
                seen_returns = set()
                for _e in cdnr:
                    for _f in ("refReference",):
                        _v = str(_e.get(_f) or "")
                        if _v:
                            seen_returns.add(_v)
                for _row in ledger_col.find(
                    {"store_id": active_store, "type": "ISSUED"},
                    {"ref": 1, "reason": 1},
                ) if ledger_col is not None else []:
                    for _f in ("ref", "reason"):
                        _v = str(_row.get(_f) or "")
                        for _tok in _v.replace(",", " ").split():
                            if _tok.startswith("RET-"):
                                seen_returns.add(_tok.strip(".:;"))

                for ret in returns_col.find(
                    {
                        "store_id": active_store,
                        "status": {"$in": ["COMPLETED", "completed"]},
                    }
                ):
                    if not isinstance(ret, dict):
                        continue
                    rid = str(ret.get("return_id") or "")
                    if rid and rid in seen_returns:
                        continue
                    gb = ret.get("gst_breakup") or {}
                    if not isinstance(gb, dict):
                        continue
                    r_tax = round(float(gb.get("tax") or 0.0), 2)
                    r_taxable = round(float(gb.get("taxable") or 0.0), 2)
                    if r_tax <= 0 and r_taxable <= 0:
                        continue

                    # Period filter. `returns.created_at` is written by
                    # BaseRepository as a real Date, unlike the ledger's ISO
                    # string, so compare in the frame it is actually stored in.
                    r_when = ret.get("created_at")
                    try:
                        if isinstance(r_when, str):
                            if not (from_dt.isoformat() <= r_when < to_dt.isoformat()):
                                continue
                        elif r_when is not None:
                            if not (from_dt <= r_when < to_dt):
                                continue
                        else:
                            continue
                    except (TypeError, ValueError):
                        continue

                    cust_id = str(ret.get("customer_id", ""))
                    cust_info = cust_map.get(cust_id, {})
                    cust_state = cust_info.get("state", "") or store_state
                    # Same resolver GSTR-3B Table 3.1(a) uses: the parent
                    # order's stamp first, the customer's state only as the
                    # fallback. Re-deriving it here is how the two returns
                    # ended up reversing different heads for one refund.
                    is_inter = _return_interstate_flag(
                        db, ret, store_state, _cdnr_inter_cache,
                        fallback_state=cust_info.get("state", "") or "",
                    )
                    if is_inter:
                        r_cgst = r_sgst = 0.0
                        r_igst = r_tax
                    else:
                        r_cgst = round(r_tax / 2, 2)
                        r_sgst = round(r_tax - r_cgst, 2)
                        r_igst = 0.0

                    r_rate = _cn_bucket_rate(gb.get("gst_rate"), r_tax, r_taxable)
                    r_place = cust_state or store_state or "Unknown"

                    cdnr.append(
                        {
                            "refReference": (rid or "RET")[:16],
                            "creditNoteDate": (
                                r_when[:10]
                                if isinstance(r_when, str)
                                else (
                                    ist_date_str(r_when)
                                    if r_when is not None
                                    else month + "-01"
                                )
                            ),
                            "customerId": cust_id,
                            "customerName": cust_info.get("name", "")
                            or str(ret.get("customer_name", "") or ""),
                            "customerGSTIN": cust_info.get("gstin", ""),
                            "customerState": r_place,
                            "placeOfSupply": r_place,
                            "grossValue": round(float(gb.get("gross") or 0.0), 2),
                            "taxableValue": r_taxable,
                            "cgst": r_cgst,
                            "sgst": r_sgst,
                            "igst": r_igst,
                            "taxValue": r_tax,
                            "hsnCode": "9004",
                            "gstRate": r_rate,
                            "sourceReturn": rid,
                        }
                    )
        except Exception:
            pass

    # Build HSN summary by aggregating sales and deducting credit notes
    for inv in b2b + b2cl:
        if not isinstance(inv, dict):
            continue
        if inv.get("deemedSupply"):
            # Transfer deemed-supply rows carry PER-LINE HSN detail (a transfer
            # can mix 5% frames with 18% sunglasses) -- merged from
            # transfer_hsn_lines below; skipping the single row-level HSN here
            # avoids double-counting and rate-lumping.
            continue
        hsn = str(inv.get("hsnCode", "9004"))
        rate = float(inv.get("gstRate", 0))
        key = f"{hsn}|{rate}"
        if key not in hsn_by_rate:
            hsn_by_rate[key] = {
                "hsnCode": hsn,
                "gstRate": int(rate),
                "taxableValue": 0.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0,
            }
        hsn_by_rate[key]["taxableValue"] += float(inv.get("taxableValue", 0))
        hsn_by_rate[key]["cgst"] += float(inv.get("cgst", 0))
        hsn_by_rate[key]["sgst"] += float(inv.get("sgst", 0))
        hsn_by_rate[key]["igst"] += float(inv.get("igst", 0))

    for b2cs_row in b2cs:
        if not isinstance(b2cs_row, dict):
            continue
        hsn = "9004"
        rate = float(b2cs_row.get("gstRate", 0))
        key = f"{hsn}|{rate}"
        if key not in hsn_by_rate:
            hsn_by_rate[key] = {
                "hsnCode": hsn,
                "gstRate": int(rate),
                "taxableValue": 0.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0,
            }
        hsn_by_rate[key]["taxableValue"] += float(b2cs_row.get("taxableValue", 0))
        hsn_by_rate[key]["cgst"] += float(b2cs_row.get("cgst", 0))
        hsn_by_rate[key]["sgst"] += float(b2cs_row.get("sgst", 0))
        hsn_by_rate[key]["igst"] += float(b2cs_row.get("igst", 0))

    # NEW-GST-TRANSFER-OUTWARD: merge the transfer deemed-supply PER-LINE HSN
    # detail (the b2b/b2cl loop above skipped these rows).
    for t_line in transfer_hsn_lines:
        if not isinstance(t_line, dict):
            continue
        hsn = str(t_line.get("hsn") or "9004")
        rate = float(t_line.get("gst_rate", 0) or 0)
        key = f"{hsn}|{rate}"
        if key not in hsn_by_rate:
            hsn_by_rate[key] = {
                "hsnCode": hsn,
                "gstRate": int(rate),
                "taxableValue": 0.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0,
            }
        hsn_by_rate[key]["taxableValue"] += float(t_line.get("taxable", 0) or 0)
        hsn_by_rate[key]["cgst"] += float(t_line.get("cgst", 0) or 0)
        hsn_by_rate[key]["sgst"] += float(t_line.get("sgst", 0) or 0)
        hsn_by_rate[key]["igst"] += float(t_line.get("igst", 0) or 0)

    for cn in cdnr:
        if not isinstance(cn, dict):
            continue
        hsn = str(cn.get("hsnCode", "9004"))
        rate = float(cn.get("gstRate", 0))
        key = f"{hsn}|{rate}"
        if key in hsn_by_rate:
            hsn_by_rate[key]["taxableValue"] -= float(cn.get("taxableValue", 0))
            hsn_by_rate[key]["cgst"] -= float(cn.get("cgst", 0))
            hsn_by_rate[key]["sgst"] -= float(cn.get("sgst", 0))
            hsn_by_rate[key]["igst"] -= float(cn.get("igst", 0))

    hsn_summary = [
        {
            "hsnCode": v["hsnCode"],
            "gstRate": v["gstRate"],
            "taxableValue": round(max(0, v["taxableValue"]), 2),
            "cgst": round(max(0, v["cgst"]), 2),
            "sgst": round(max(0, v["sgst"]), 2),
            "igst": round(max(0, v["igst"]), 2),
        }
        for v in hsn_by_rate.values()
        if v["taxableValue"] > 0 or v["cgst"] > 0 or v["sgst"] > 0 or v["igst"] > 0
    ]

    total_invoices = len(b2b) + len(b2cl) + len(b2cs_map)
    total_taxable = (
        sum(i["taxableValue"] for i in b2b)
        + sum(i["taxableValue"] for i in b2cl)
        + sum(v["taxableValue"] for v in b2cs)
    )
    total_tax = (
        sum(i["totalTax"] for i in b2b)
        + sum(i["totalTax"] for i in b2cl)
        + sum(v["totalTax"] for v in b2cs)
    )

    return {
        "period": month,
        "gstin": store_gstin,
        "legalName": store_legal_name,
        "storeState": store_state,
        "totalInvoices": total_invoices,
        "totalTaxableValue": round(total_taxable, 2),
        "totalTax": round(total_tax, 2),
        "b2b": b2b,
        "b2cl": b2cl,
        "b2cs": b2cs,
        "cdnr": cdnr,
        "hsnSummary": hsn_summary,
        "validation": {
            "ok": len(validation_issues) == 0,
            "issueCount": len(validation_issues),
            "issues": validation_issues[:50],
        },
    }


@router.get("/gstr1")
async def gstr1_report(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GSTR-1 report (IMS internal shape). See _compute_gstr1 for details."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    return _compute_gstr1(month, active_store)


@router.get("/gstr1/gstn-json")
async def gstr1_gstn_json(
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
    """GSTR-1 shaped for the GST portal's offline upload tool.

    Reuses _compute_gstr1 (no duplicated aggregation) and runs the pure
    mapping in services/gstn_export.py. The accountant uploads the
    resulting JSON via gst.gov.in -> Returns Offline Tool -> Import.

    `month` accepts the IMS canonical "YYYY-MM". For convenience a numeric
    `month` (1-12) plus `year` is also accepted and normalised. `entity_id`
    is accepted for forward-compat but store_id remains the resolution key.
    """
    from ...services.gstn_export import to_gstr1_json

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    period = _normalise_period(month, year)
    data = _compute_gstr1(period, active_store)
    try:
        return to_gstr1_json(data, gstin=data.get("gstin", ""), period=period)
    except Exception:
        # Fail soft: never 500 on a shaping bug — return an empty skeleton.
        return to_gstr1_json({}, gstin="", period=period)


