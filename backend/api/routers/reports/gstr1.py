"""GSTR-1 (outward supplies): computation plus the two endpoints."""

import re

from ...services import store_credit_ledger as scl  # THE credit-note type rule
from fastapi import Depends, HTTPException, Query
from typing import Optional
from ...utils.event_time import parse_event_utc_naive
from ...utils.ist import (
    instant_in_window_query,
    ist_date_str,
    ist_month_window_utc,
)
from calendar import monthrange
from ..auth import require_roles
from ...services.cost_mask import AP_ROLES
from ...dependencies import store_in_view
from ...dependencies import (
    validate_store_access,
)
from ...utils.online_gst import order_place_of_supply
from ...services.org_validation import resolve_state_code, state_name
from ._shared import (
    _cdnr_note_number,
    _credit_note_date_ist,
    _gstr1_bill_number,
    logger,
    router,
)
from database.repositories.order_repository import DOCUMENT_LOG
from ...utils.real_sale import is_real_sale
from .gst_base import (
    B2CL_INVOICE_THRESHOLD,
    real_sale_match,
    _get_raw_db,
    _normalise_period,
    CUSTOMER_SUPPLY_FIELDS,
    UnfileableOrder,
    _money,
    _order_rate_lines,
    _order_supply,
    _order_taxable_and_tax,
    _alloc,
    _rate_heads,
)
from ...services.gst_rates import split_gst
from .gst_itc import (
    _cn_bucket_rate,
    _cn_foreign_store,
    _cn_parent_held,
    _debit_notes,
    _itc_store_scope,
    _ledger_row_return_doc,
    _order_held_off_returns,
    _return_supply,
    _split_transfer_bills,
    _transfer_b2b_rows,
    _transfer_cdnr_rows,
    _transfer_outward_bills,
)

def _compute_gstr1(month: str, active_store: str, shops=None) -> dict:
    """Compute the IMS GSTR-1 return for a month: THE return of
    `active_store`'s GSTIN -- GSTR-1 is filed per GSTIN, so it carries every
    shop on that GSTIN (gst_itc._itc_store_scope, the scope GSTR-3B files:
    BV-PUN-01 + BV-ONLINE-01 file one return) and never a shop of another
    GSTIN. `shops` narrows it to those shops (the Cross-Check of a reader who
    sees one shop of a shared GSTIN); omitted, the whole GSTIN.

    Extracted from the `/gstr1` endpoint so both the JSON-report endpoint
    and the GSTN portal-export endpoint can share the SAME aggregation
    (no duplicated query logic). Pure-ish: reads MongoDB, returns a dict.
    Raises HTTPException(400) on a malformed `month`; a database read failure
    RAISES (the doors turn it into a 503 -- _gstr1_return), never a partial
    report.

    Classifies invoices into:
      - B2B  : orders where the customer has a GSTIN on file
      - B2CL : INTER-state orders to consumers with invoice value above
               B2CL_INVOICE_THRESHOLD (gst_base; the CA confirms the value)
      - B2CS : consolidated summary of remaining consumer invoices, every
               intra-state one whatever its value
    Each B2B / B2CL row carries `rateLines`, one per GST rate on the invoice
    (the portal's `itms[]`), from the lines' stamped tax (_order_rate_lines).

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
    # the month boundaries must be shifted through ist_month_window_utc. With the
    # old UTC-frame window an invoice minted 01-Jun 02:00 IST (= 31-May 20:30
    # UTC) filed into MAY's GSTR-1, contradicting its IST-minted Rule 46(b)
    # serial; on 1-Apr it even fell into the prior FY. The half-open
    # [from_dt, to_dt) window also closes the old `$lte 23:59:59` 1-second hole.
    try:
        year, mon = int(month[:4]), int(month[5:7])
        from_dt, to_dt = ist_month_window_utc(year, mon)
    except Exception:
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

    b2b: list = []
    b2cl: list = []
    b2cs_map: dict = {}
    validation_issues: list = []
    # NEW-GST-TRANSFER-OUTWARD: per-line HSN detail of the sender-side transfer
    # deemed-supply bills (filled below; merged into the HSN summary instead of
    # the row-level dominant HSN so mixed 5%/18% transfers stay exact).
    transfer_hsn_lines: list = []
    # S3: the sender's transfer credit notes (short delivery), filed in CDNR.
    transfer_note_rows: list = []
    # The same per-line HSN detail of the B2B / B2CL sales (one per line and
    # rate, _order_rate_lines), so a mixed-rate invoice is rate-exact there too.
    sale_hsn_lines: list = []

    # Store header (gstin + legalName + home state for intra/inter split)
    store_gstin = ""
    store_legal_name = ""
    store_state = ""
    store_doc = None

    filing = [active_store]
    db = _get_raw_db()
    if db is not None:
        # A read failure RAISES (the doors answer 503, _gstr1_return) -- never a
        # report short of the orders, credit notes or returns it could not read.
        store_doc = db["stores"].find_one({"store_id": active_store})
        # THE GSTIN this return files for and every shop on it -- the scope
        # GSTR-3B reads, so the two returns of one GSTIN cover the same shops.
        _entity, store_gstin, gstin_shops = _itc_store_scope(db, active_store)
        filing = sorted(shops) if shops is not None else gstin_shops
        # Each sale's OWN shop decides its supply (GSTR-3B's rule): a shop
        # with no GSTIN of its own is in its registration's state.
        shop_docs = {
            sd.get("store_id"): {**sd, "gstin": sd.get("gstin") or store_gstin}
            for sd in db["stores"].find(
                {"store_id": {"$in": filing}},
                {"_id": 0, "store_id": 1, "state_code": 1, "state": 1, "gstin": 1},
            )
        }
        if store_doc:
            store_gstin = store_gstin or str(store_doc.get("gstin", "") or "")
            store_legal_name = str(
                store_doc.get("store_name") or store_doc.get("name", "") or ""
            )
            store_state = str(store_doc.get("state", "") or "")
        store_state = store_state or next(
            (str(d["state"]) for d in shop_docs.values() if d.get("state")), ""
        )

        def _shop_doc(sid):
            return shop_docs.get(sid) or store_doc

        orders_col = db["orders"]
        customers_col = db["customers"]

        # Build a lookup: customer_id -> the customer fields the place-of-
        # supply rule reads. A dropped customer read would file a B2B sale as
        # B2C, so it raises too.
        cust_docs: dict = {}
        cust_map: dict = {}
        for cust in customers_col.find({}, CUSTOMER_SUPPLY_FIELDS):
            cid = str(cust.get("customer_id", ""))
            cust_docs[cid] = cust
            cust_map[cid] = {
                "gstin": str(cust.get("gstin", "") or ""),
                "name": str(cust.get("name", "") or ""),
                "state": str(cust.get("state", "") or ""),
            }

        # Fetch completed orders in the date range. Filter uses real
        # datetime objects to match BaseRepository which writes
        # `created_at` as a Date — comparing a Date to an ISO string
        # silently never matches (this was the original GSTR bug).
        query = {
            "store_id": {"$in": filing},
            # The GST-document view (PR #1264): it also files the credit notes,
            # so an invoice cancelled behind a whole-invoice note stays filed.
            **real_sale_match(db, gst_documents=True),
            "created_at": {"$gte": from_dt, "$lt": to_dt},
        }

        for order in orders_col.find(query):
            # A routed online order its booking HELD on the seller check
            # (no shop named, a shop or split leg without its own state's
            # GSTIN, a split across GSTINs) is never filed under this
            # GSTIN until Re-map re-routes it -- nor are its credit notes
            # (below), nor is it in GSTR-3B or Tally (the same question).
            held = _order_held_off_returns(db, order)
            if held:
                validation_issues.append(
                    {
                        "level": "error",
                        "invoice": order.get("invoice_number")
                        or order.get("order_number"),
                        "issue": "Not filed: " + held["message"],
                    }
                )
                continue
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
            # Read tolerantly (Decimal128, '1,050.00'); an unreadable amount
            # raises naming the order (_money), never files as 0.
            invoice_value = _money(
                order.get("grand_total", order.get("total_amount", 0)), order
            )
            taxable_value, total_tax = _order_taxable_and_tax(order)

            # Intra vs inter-state: the head the tax invoice PRINTED
            # (_order_supply -- the order's own stamp, else the invoice's
            # state-code rule, org_validation.supply_interstate).
            is_inter_state, pos_code, pos_assumed = _order_supply(
                order, _shop_doc(order.get("store_id")), cust_docs.get(cust_id)
            )
            items = order.get("items") or []
            # One item row per GST rate (GSTN `itms[]`), from the tax stamped
            # on the order lines (_order_rate_lines): a 5% frame + an 18%
            # sunglass invoice files at both rates, never at the first line's.
            sale_qtys: list = []
            sale_lines = _order_rate_lines(
                items, taxable_value, total_tax, order, validation_issues, qtys=sale_qtys
            )
            if not sale_lines:  # a zero-value order files nothing (as GSTR-3B)
                continue
            rate_heads = _rate_heads(sale_lines, is_inter_state)
            rate_lines = [
                {k: rl[k] for k in ("rate", "taxable", "cgst", "sgst", "igst")}
                for rl in rate_heads
            ]
            igst = round(sum(rl["igst"] for rl in rate_lines), 2)
            cgst = round(sum(rl["cgst"] for rl in rate_lines), 2)
            sgst = round(sum(rl["sgst"] for rl in rate_lines), 2)

            is_b2cl = (
                not customer_gstin
                and is_inter_state
                and invoice_value > B2CL_INVOICE_THRESHOLD
            )
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
            # The order's OWN persisted place of supply first (an online
            # order's delivery state -- the same record its IGST flag
            # above comes from); the customer doc's state is a returning
            # buyer's FIRST delivery state and filed IGST rows with the
            # supplier's own state as the place of supply.
            place_of_supply = (
                order_place_of_supply(order)
                or cust_info.get("state", "")
                or state_name(pos_code)
                or pos_code
                or store_state
                or "Unknown"
            )

            # Row-level HSN / rate: the largest line's, for the screen only.
            # The filed figures are the per-rate `rateLines` (portal itms) and
            # the per-line HSN summary (sale_hsn_lines below).
            _big = max(sale_lines, key=lambda ln: ln[2])
            hsn_code, gst_rate_dominant = _big[1], _big[0]

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
                "rateLines": rate_lines,
                # THE place of supply filed (portal `pos`): the state code
                # the place-of-supply rule gave this sale (_order_supply).
                "placeOfSupplyCode": pos_code,
            }

            # Every sale's HSN summary rows come from its own lines, per HSN
            # and rate -- the same key a credit note reverses (_cn_hsn_lines).
            # Table 12 files B2B (a buyer GSTIN) and B2C apart, each with the
            # pieces sold (_rate_heads keeps each rate's lines in sale order).
            qty_by_rate: dict = {}
            for ln, q in zip(sale_lines, sale_qtys):
                qty_by_rate.setdefault(ln[0], []).append(q)
            for rl in rate_heads:
                for (hsn, line_taxable, l_cgst, l_sgst, l_igst), q in zip(
                    rl["lines"], qty_by_rate.get(rl["rate"], [])
                ):
                    sale_hsn_lines.append(
                        {
                            "hsn": hsn,
                            "gst_rate": rl["rate"],
                            "section": "B2B" if customer_gstin else "B2C",
                            "qty": q,
                            "taxable": line_taxable,
                            "cgst": l_cgst,
                            "sgst": l_sgst,
                            "igst": l_igst,
                        }
                    )

            if customer_gstin:
                # B2B: registered business with GSTIN
                b2b.append(
                    {
                        **base_invoice,
                        "customerGSTIN": customer_gstin,
                        "customerState": customer_state or store_state,
                    }
                )
            elif is_b2cl:
                # B2CL: an INTER-state consumer invoice above the threshold.
                # An intra-state one of any value is B2CS (below).
                b2cl.append(
                    {
                        **base_invoice,
                        "customerState": customer_state or store_state,
                    }
                )
                # An "out-of-state" B2CL whose buyer state was never
                # resolved is technically required to have one — flag it.
                if pos_assumed:
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
                for rl in rate_heads:
                    rate = rl["rate"]
                    # The portal's key: (supply type, place of supply,
                    # rate), the place by its state CODE -- a POS row names
                    # the state ('Jharkhand'), an online row its persisted
                    # code ('20'); one consolidated row, never two.
                    key = (
                        f"{pos_code or resolve_state_code(place_of_supply) or place_of_supply}"
                        f"|{rate}|{is_inter_state}"
                    )
                    if key not in b2cs_map:
                        b2cs_map[key] = {
                            "placeOfSupply": place_of_supply,
                            "placeOfSupplyCode": pos_code,
                            "gstRate": rate,
                            "taxableValue": 0.0,
                            "cgst": 0.0,
                            "sgst": 0.0,
                            "igst": 0.0,
                            "totalTax": 0.0,
                        }
                    b2cs_map[key]["taxableValue"] += rl["taxable"]
                    b2cs_map[key]["totalTax"] += rl["tax"]
                    b2cs_map[key]["igst"] += rl["igst"]
                    b2cs_map[key]["cgst"] += rl["cgst"]
                    b2cs_map[key]["sgst"] += rl["sgst"]

            # Validation: B2B without GSTIN — caught by the absence
            # of customer_gstin above. Add an explicit warning for
            # high-value invoices missing it.
            if invoice_value > B2CL_INVOICE_THRESHOLD and not customer_gstin:
                validation_issues.append(
                    {
                        "level": "info",
                        "invoice": bill_number,
                        "issue": f"Invoice > Rs {B2CL_INVOICE_THRESHOLD:,} without "
                        "customer GSTIN — confirm B2C status",
                    }
                )

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
            _t_bills, _t_notes = _split_transfer_bills([
                b
                for _shop in filing
                for b in _transfer_outward_bills(
                    db, _shop, year, mon, monthrange(year, mon)[1]
                )
            ])
            # S3: a transfer's credit notes (short delivery) go to CDNR below.
            transfer_note_rows = _transfer_cdnr_rows(_t_notes)
            _t_rows, transfer_hsn_lines = _transfer_b2b_rows(_t_bills)
            # The transfer tax invoice a bill carries (minted at ship): its
            # row files the date printed on it, and what it was issued for
            # (the units SHIPPED) is checked against what the bill books.
            _t_issued = {
                _b.get("source_transfer_id"): _b for _b in _t_bills if _b.get("tax_invoice_date")
            }
            for _t_row in _t_rows:
                # The transfer tax invoice number against GSTN's rule, the
                # same check every sale's number gets (an old TRF/ number is
                # over 16 characters).
                _gstr1_bill_number(
                    {"invoice_number": _t_row.get("invoiceNumber")}, validation_issues
                )
                _t_bill = _t_issued.get(_t_row.get("sourceTransferId"))
                if _t_bill:
                    _t_row["invoiceDate"] = str(_t_bill["tax_invoice_date"])[:10]
                    _issued = round(float(_t_bill.get("challan_value") or 0), 2)
                    if abs(_issued - _t_row["taxableValue"]) > 0.01:
                        validation_issues.append(
                            {
                                "level": "warn",
                                "invoice": _t_row.get("invoiceNumber"),
                                "issue": (
                                    f"IMS files the RECEIVED value Rs "
                                    f"{_t_row['taxableValue']:.2f} (taxable) under "
                                    f"{_t_row.get('invoiceNumber')}; the printed "
                                    "invoice and e-way bill show the SHIPPED value "
                                    f"Rs {_issued:.2f}. Ask the CA whether to amend."
                                ),
                            }
                        )
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
        except Exception as exc:  # noqa: BLE001
            # Fail LOUDLY (GST review 2026-10-19, M7): GSTR-3B already 503s
            # when transfers cannot be read; dropping every deemed supply
            # from GSTR-1 here would file the two returns differently.
            logger.exception("[GSTR-1] %s %s: transfers could not be read", active_store, month)
            raise HTTPException(
                status_code=503, detail="GSTR-1 transfer invoices could not be read; please retry."
            ) from exc

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
    cdnr: list = list(transfer_note_rows)
    # Per-report order->interstate cache, shared by the ledger pass and the
    # in-store returns pass so one refund can never resolve two heads.
    _cdnr_inter_cache: dict = {}
    _held_cache: dict = {}  # order_id -> parent held on the seller check

    def _held_cn_issue(ref, ret_doc) -> dict:
        return {
            "level": "error",
            "invoice": str(ref or (ret_doc or {}).get("return_id") or ""),
            "issue": "Not filed: this credit note reverses order "
            f"{(ret_doc or {}).get('order_number') or (ret_doc or {}).get('order_id')}, "
            "whose sale is held off this return on its seller (GSTIN) check.",
        }

    _parent_cache: dict = {}  # order_id -> (the sale's order doc, its rate lines)

    def _parent(oid: str) -> tuple:
        """The PARENT sale and its (rate, hsn, taxable, tax) lines -- the same
        _order_rate_lines its own GSTR-1 rows were filed from. (None, []) when
        the note names no order or the order is gone. A read failure raises."""
        if not oid:
            return None, []
        if oid not in _parent_cache:
            po = db.get_collection("orders").find_one({"order_id": oid})
            _parent_cache[oid] = (po, (
                _order_rate_lines(po.get("items") or [], *_order_taxable_and_tax(po), po)
                if po
                else []
            ))
        return _parent_cache[oid]

    def _parent_lines(oid: str) -> list:
        return _parent(oid)[1]

    def _returned_units(ret_doc, oid) -> dict:
        """(rate, hsn) -> pieces a note's return brought back: each return
        line's ``return_qty`` on the HSN of the sale line it reverses (by
        order_item_id, else product_id -- the return door's own match). {} for
        a value-only note (no return lines: a price correction)."""
        po = _parent(oid)[0]
        if not isinstance(ret_doc, dict) or not po:
            return {}
        by_item, by_prod = {}, {}
        for it in po.get("items") or []:
            if isinstance(it, dict):
                by_item.setdefault(str(it.get("item_id") or it.get("id") or ""), it)
                by_prod.setdefault(str(it.get("product_id") or ""), it)
        units: dict = {}
        for ln in ret_doc.get("items") or []:
            if not isinstance(ln, dict):
                continue
            orig = by_item.get(str(ln.get("order_item_id") or "")) or by_prod.get(
                str(ln.get("product_id") or "")
            )
            if not orig:
                continue
            rate = orig.get("gst_rate")
            try:
                qty = float(ln.get("return_qty") or 0)
                rate = int(round(float(str(ln.get("gst_rate") if rate is None else rate).rstrip("% "))))
            except (TypeError, ValueError):
                continue
            if qty > 0:
                key = (rate, str(orig.get("hsn_code") or "9004"))
                units[key] = units.get(key, 0.0) + qty
        return units

    def _cn_hsn_lines(ref, oid, buckets, heads, units=None) -> list:
        """A credit note's HSN summary lines: each (rate, taxable) bucket
        spread over its PARENT sale's lines AT THAT RATE, by those lines' HSN
        (pro rata to the sale's taxable), and the note's own CGST/SGST/IGST
        pro rata to taxable -- so the note reverses the very HSN rows its sale
        filed. A bucket that matches no line of its sale is a reported error,
        never deducted from a guessed HSN."""
        pieces = []  # (hsn, rate, taxable)
        sale = _parent_lines(oid)
        for rate, taxable in buckets:
            at_rate = [ln for ln in sale if ln[0] == rate and ln[2] > 0]
            if not at_rate:
                validation_issues.append(
                    {
                        "level": "error",
                        "invoice": str(ref),
                        "issue": (
                            f"Credit note: Rs {taxable:,.2f} at {rate}% matches no line of "
                            + (f"its sale (order {oid})" if oid else "a sale (no order linked)")
                            + "; it is NOT deducted from the HSN summary -- the CA must "
                            "allocate it to an HSN."
                        ),
                    }
                )
                continue
            for (_r, hsn, _t, _x), part in zip(
                at_rate, _alloc(taxable, [ln[2] for ln in at_rate])
            ):
                pieces.append((hsn, rate, part))
        if not pieces:
            return []
        weights = [p[2] for p in pieces]
        cg, sg, ig = (_alloc(h, weights) for h in heads)
        units = dict(units or {})  # the pieces go on the first line of their (rate, HSN)
        return [
            {"hsn": h, "gst_rate": r, "qty": units.pop((r, h), 0.0),
             "taxable": t, "cgst": c, "sgst": g, "igst": i}
            for (h, r, t), c, g, i in zip(pieces, cg, sg, ig)
        ]

    def _note_rate_lines(buckets, heads) -> list:
        """A note's portal ``itms``: one line per rate, its heads shared pro
        rata to each bucket's tax (rate x taxable) -- the invoice's rateLines
        shape, so a 5% + 18% note files two self-consistent items."""
        w = [r * t for r, t in buckets]
        cg, sg, ig = (_alloc(h, w) for h in heads)
        return [
            {"rate": r, "taxable": t, "cgst": c, "sgst": g, "igst": i}
            for (r, t), c, g, i in zip(buckets, cg, sg, ig)
        ]

    def _note_section(gstin, is_inter, oid) -> str:
        """Where a note files in GSTR-1 (owner Option B, 2026-08-09): CDNR,
        document-wise, only for a buyer with a GSTIN; an unregistered buyer's
        note on a B2CL sale (inter-state, above the B2CL limit -- the sale
        side's own rule) in CDNUR; every other consumer note nets into its
        B2CS row (state, rate) in the export."""
        if gstin:
            return "CDNR"
        po = _parent(oid)[0]
        if is_inter and po and _money(
            po.get("grand_total", po.get("total_amount", 0)), po
        ) > B2CL_INVOICE_THRESHOLD:
            return "CDNUR"
        return "B2CS"

    if db is not None:
        # Process credit notes from returns/refunds in credit_note_ledger
        # `get_collection(...) or db[...]` here raised NotImplementedError on
        # every real pymongo Collection (no truth value) and the swallow below
        # it filed GSTR-1 with NO credit notes at all. A read failure now
        # RAISES (the doors answer 503) like GSTR-3B's _credit_note_totals.
        ledger_col = db.get_collection("credit_note_ledger")
        if ledger_col is not None:
            # Query credit notes issued in the period. The ledger writes
            # created_at as an ISO STRING (legacy naive-UTC, or explicit
            # '+00:00' since A13); the match also covers a BSON-date row.
            # Half-open [from_dt, to_dt), the same match GSTR-3B uses.
            ledger_query = {
                "store_id": {"$in": filing},
                "type": scl.CREDIT_NOTE_TYPE,
                **instant_in_window_query("created_at", from_dt, to_dt),
            }
            # Allow for date stored as datetime or ISO string
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
                if _cn_foreign_store(_ret_doc, filing):
                    continue
                # The refund of a sale this report refused to file
                # (held on the seller check) is not filed either.
                if _cn_parent_held(db, _ret_doc, _held_cache):
                    validation_issues.append(_held_cn_issue(entry.get("ref"), _ret_doc))
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
                # The place of supply is ALWAYS the sale's (_return_supply):
                # a note filed IGST must name the buyer's state, not ours.
                is_inter, cn_code = _return_supply(
                    db,
                    _ret_doc or {"customer_id": cust_id},
                    _shop_doc((_ret_doc or {}).get("store_id") or entry.get("store_id")),
                    _cdnr_inter_cache,
                )
                entry_interstate = entry.get("interstate")
                if isinstance(entry_interstate, bool):
                    is_inter = entry_interstate
                # THE split rule (gst_rates.split_gst), the invoice's: the
                # heads always add up to the note's tax.
                cn_cgst, cn_sgst, cn_igst = split_gst(cn_tax, is_inter)

                # The note's own number, verbatim (flagged when it
                # breaks GSTN's rule) -- see _cdnr_note_number.
                ref = _cdnr_note_number(entry, validation_issues)
                cn_date = _credit_note_date_ist(
                    entry.get("created_at")
                ) or (month + "-01")

                # Credit notes carry the items' HSN/rate. Prefer the
                # rate stamped on the ledger row (Shopify refund +
                # in-store credit note stamp gst_rate); a legacy row
                # without one derives it from its own tax/taxable --
                # NEVER a fabricated 18% that raided the wrong HSN
                # bucket.
                cn_rate = _cn_bucket_rate(
                    entry.get("gst_rate"), cn_tax, cn_taxable
                )
                _cn_oid = str((_ret_doc or {}).get("order_id") or "")
                cn_hsn_lines = _cn_hsn_lines(
                    ref,
                    _cn_oid,
                    [(cn_rate, cn_taxable)],
                    (cn_cgst, cn_sgst, cn_igst),
                    _returned_units(_ret_doc, _cn_oid),
                )
                cn_hsn = cn_hsn_lines[0]["hsn"] if cn_hsn_lines else ""
                cn_place = state_name(cn_code) or cn_code or cust_state or store_state or "Unknown"

                cn_entry = {
                    "refReference": ref,
                    "noteType": "C",
                    "noteSection": _note_section(cust_gstin, is_inter, _cn_oid),
                    "rateLines": _note_rate_lines(
                        [(cn_rate, cn_taxable)], (cn_cgst, cn_sgst, cn_igst)
                    ),
                    "creditNoteDate": cn_date,
                    "customerId": cust_id,
                    "customerName": cust_info.get("name", ""),
                    "customerGSTIN": cust_gstin,
                    "customerState": cn_place,
                    "placeOfSupply": cn_place,
                    "placeOfSupplyCode": cn_code,
                    "grossValue": round(gross_refund, 2),
                    "taxableValue": cn_taxable,
                    "cgst": cn_cgst,
                    "sgst": cn_sgst,
                    "igst": cn_igst,
                    "taxValue": round(cn_tax, 2),
                    "hsnCode": cn_hsn,
                    "gstRate": cn_rate,
                    "hsnLines": cn_hsn_lines,
                }
                cdnr.append(cn_entry)

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
                {"store_id": {"$in": filing}, "type": scl.CREDIT_NOTE_TYPE},
                {"ref": 1, "reason": 1},
            ) if ledger_col is not None else []:
                for _f in ("ref", "reason"):
                    _v = str(_row.get(_f) or "")
                    for _tok in _v.replace(",", " ").split():
                        if _tok.startswith("RET-"):
                            seen_returns.add(_tok.strip(".:;"))

            for ret in returns_col.find(
                {
                    "store_id": {"$in": filing},
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

                # Period filter: the naive-UTC instant whatever the stored
                # shape (BSON naive/aware, ISO string with or without an
                # offset), half-open -- the same test GSTR-3B applies.
                r_when = parse_event_utc_naive(ret.get("created_at"))
                if r_when is None or not (from_dt <= r_when < to_dt):
                    continue
                if _cn_parent_held(db, ret, _held_cache):
                    validation_issues.append(_held_cn_issue(rid, ret))
                    continue

                cust_id = str(ret.get("customer_id", ""))
                cust_info = cust_map.get(cust_id, {})
                cust_state = cust_info.get("state", "") or store_state
                # Same resolver GSTR-3B Table 3.1(a) uses: the parent
                # order's stamp first, the customer's state only as the
                # fallback. Re-deriving it here is how the two returns
                # ended up reversing different heads for one refund.
                is_inter, r_code = _return_supply(
                    db, ret, _shop_doc(ret.get("store_id")), _cdnr_inter_cache
                )
                r_cgst, r_sgst, r_igst = split_gst(r_tax, is_inter)

                r_rate = _cn_bucket_rate(gb.get("gst_rate"), r_tax, r_taxable)
                # The return's own per-rate back-out (returns_engine.
                # gst_breakup_lines `by_rate`) when it reconciles; else one
                # bucket at the note's rate.
                by_rate = gb.get("by_rate") if isinstance(gb.get("by_rate"), dict) else {}
                r_buckets = [
                    (int(round(float(k))), round(float((v or {}).get("taxable") or 0), 2))
                    for k, v in by_rate.items()
                ]
                if not r_buckets or abs(sum(t for _, t in r_buckets) - r_taxable) > 0.05:
                    r_buckets = [(r_rate, r_taxable)]
                r_ref = _cdnr_note_number(
                    {"note_number": ret.get("credit_note_number"), "ref": rid},
                    validation_issues,
                )
                r_oid = str(ret.get("order_id") or "")
                r_hsn_lines = _cn_hsn_lines(
                    r_ref, r_oid, r_buckets, (r_cgst, r_sgst, r_igst),
                    _returned_units(ret, r_oid),
                )
                r_place = state_name(r_code) or r_code or cust_state or store_state or "Unknown"

                cdnr.append(
                    {
                        # The return's credit-note number (its shop's C
                        # series); a return before numbering: its id.
                        "refReference": r_ref,
                        "noteType": "C",
                        "noteSection": _note_section(
                            cust_info.get("gstin", ""), is_inter, r_oid
                        ),
                        "rateLines": _note_rate_lines(r_buckets, (r_cgst, r_sgst, r_igst)),
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
                        "placeOfSupplyCode": r_code,
                        "grossValue": round(float(gb.get("gross") or 0.0), 2),
                        "taxableValue": r_taxable,
                        "cgst": r_cgst,
                        "sgst": r_sgst,
                        "igst": r_igst,
                        "taxValue": r_tax,
                        "hsnCode": r_hsn_lines[0]["hsn"] if r_hsn_lines else "",
                        "gstRate": r_rate,
                        "hsnLines": r_hsn_lines,
                        "sourceReturn": rid,
                    }
                )

        # CUSTOMER DEBIT NOTES (a post-invoice change that raised the bill):
        # filed like a credit note with note type D -- CDNR / CDNUR / B2CS by
        # the same rule, per GSTIN (_debit_notes: the reader GSTR-3B adds).
        for dn in _debit_notes(
            db, filing, from_dt, to_dt, _held_cache, _cdnr_inter_cache, _shop_doc
        ):
            d = dn["doc"]
            if dn["held"]:
                validation_issues.append(_held_cn_issue(d.get("note_number"), d))
                continue
            d_ref = _cdnr_note_number(
                {"note_number": d.get("note_number"), "ref": d.get("note_number")},
                validation_issues,
            )
            d_oid = str(d.get("order_id") or "")
            d_cust = cust_map.get(str(d.get("customer_id") or ""), {})
            d_rate = _cn_bucket_rate(d.get("gst_rate"), dn["tax"], dn["taxable"])
            d_heads = (dn["cgst"], dn["sgst"], dn["igst"])
            d_hsn_lines = _cn_hsn_lines(d_ref, d_oid, [(d_rate, dn["taxable"])], d_heads)
            d_place = state_name(dn["code"]) or dn["code"] or store_state or "Unknown"
            cdnr.append(
                {
                    "refReference": d_ref,
                    "noteType": "D",
                    "noteSection": _note_section(d_cust.get("gstin", ""), dn["inter"], d_oid),
                    "rateLines": _note_rate_lines([(d_rate, dn["taxable"])], d_heads),
                    "creditNoteDate": _credit_note_date_ist(d.get("created_at")) or (month + "-01"),
                    "customerId": str(d.get("customer_id") or ""),
                    "customerName": d_cust.get("name", "") or str(d.get("customer_name") or ""),
                    "customerGSTIN": d_cust.get("gstin", ""),
                    "customerState": d_place,
                    "placeOfSupply": d_place,
                    "placeOfSupplyCode": dn["code"],
                    "grossValue": round(float(d.get("amount") or 0.0), 2),
                    "taxableValue": dn["taxable"],
                    "cgst": dn["cgst"],
                    "sgst": dn["sgst"],
                    "igst": dn["igst"],
                    "taxValue": dn["tax"],
                    "hsnCode": d_hsn_lines[0]["hsn"] if d_hsn_lines else "",
                    "gstRate": d_rate,
                    "hsnLines": d_hsn_lines,
                    "originalInvoiceNumber": d.get("original_invoice_number"),
                }
            )

    # Build HSN summary by aggregating sales and deducting credit notes.
    # Every sale (B2B, B2CL, B2CS) and transfer deemed supply by its PER-LINE
    # HSN detail, per rate (a 5% frame + an 18% sunglass on one invoice, or one
    # transfer, file two HSN rows -- never one at the first line's rate).
    # Credit notes reverse the HSN rows their SALES filed (each note's
    # hsnLines, _cn_hsn_lines) -- a note whose sale was filed in an earlier
    # month opens its HSN row here and drives it below zero.
    # GSTR-1 Table 12 (CA 2026-10-23) files the summary in TWO parts, B2B (to a
    # GSTIN: B2B sales, transfer deemed supplies, registered credit notes) and
    # B2C, each row with its UQC quantity; `hsnSummary` stays the combined
    # view the screen and the cross-check read.
    for t_line in transfer_hsn_lines:
        if isinstance(t_line, dict):
            t_line["section"] = "B2B"
    cn_hsn: list = []
    dn_hsn: list = []  # a debit note ADDS to the rows its sale filed
    for cn in cdnr:
        if not isinstance(cn, dict):
            continue
        section = "B2B" if cn.get("customerGSTIN") else "B2C"
        (dn_hsn if cn.get("noteType") == "D" else cn_hsn).extend(
            {**hl, "section": section} for hl in cn.get("hsnLines") or []
        )
    sales_hsn = [
        ln for ln in sale_hsn_lines + transfer_hsn_lines + dn_hsn if isinstance(ln, dict)
    ]
    hsn_summary = _hsn_rows(sales_hsn, cn_hsn)
    hsn_b2b = _hsn_rows(
        [ln for ln in sales_hsn if ln.get("section") == "B2B"],
        [ln for ln in cn_hsn if ln["section"] == "B2B"],
        validation_issues,
        "B2B",
    )
    hsn_b2c = _hsn_rows(
        [ln for ln in sales_hsn if ln.get("section") != "B2B"],
        [ln for ln in cn_hsn if ln["section"] != "B2B"],
        validation_issues,
        "B2C",
    )

    # B2CS is filed NET of the consumer notes (owner Option B; gstn_export
    # b2cs_net_rows -- the export's own netting). The portal takes no negative
    # row: one the notes exceed files at 0, and the excess is said here.
    from ...services.gstn_export import b2cs_net_rows

    for v in b2cs_net_rows(b2cs, cdnr):
        over = {k: round(-v[k], 2) for k in ("txval", "iamt", "camt", "samt") if v[k] < -0.005}
        if over:
            validation_issues.append(
                {
                    "level": "error",
                    "invoice": f"B2CS {v['pos']} @ {v['rt']:g}% ({v['sply_ty']})",
                    "issue": "Consumer credit notes exceed this month's B2CS sales on this row by "
                    + ", ".join(f"{k} Rs {a:,.2f}" for k, a in over.items())
                    + "; the row is filed at 0 -- carry the excess with the CA.",
                }
            )

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
        # The shops this return files for: every shop on the GSTIN.
        "shops": filing,
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
        "hsnB2B": hsn_b2b,
        "hsnB2C": hsn_b2c,
        "docsIssued": (
            _docs_issued(db, active_store, from_dt, to_dt, cdnr, validation_issues)
            if db is not None else []
        ),
        "validation": {
            "ok": len(validation_issues) == 0,
            "issueCount": len(validation_issues),
            # Errors first, then warnings, then info: the first 50 shown are
            # the ones that stop a filing.
            "issues": sorted(
                validation_issues,
                key=lambda i: {"error": 0, "warn": 1}.get(i.get("level"), 2),
            )[:50],
        },
    }


# GSTR-1 Table 13: the register series -> (doc_num, the GSTN document type).
_DOC_TYPES = {
    "": (1, "Invoices for outward supply"),
    "T": (1, "Invoices for outward supply"),
    "D": (4, "Debit Note"),
    "C": (5, "Credit Note"),
    "TRF": (12, "Delivery Challan in cases other than by way of supply (excluding at S no. 9 to 11)"),
}
_SERIAL_RE = re.compile(r"^(.*?)(\d+)$")
_MAX_GAP = 1000  # a longer gap is no real run of cancelled numbers


def _docs_issued(db, store_id: str, from_dt, to_dt, cdnr=(), issues=None) -> list:
    """Table 13 (documents issued, mandatory per the CA 2026-10-23) for the
    shop's month, from the document register every number is logged in
    (order_repository.log_document_number): per series (the number up to its
    serial), each run of consecutive serials is one row -- from, to, total
    and cancelled. Each document is counted in the month its table files it:
      * an invoice ('') in its ORDER's month (created_at -- the B2B / B2CL /
        B2CS window), so Table 13 ties to the invoice tables; cancelled when
        the order is not a sale; a number minted with no order written:
        cancelled, in the month it was minted;
      * a delivery challan (TRF) in the month its goods LEFT (shipped_at;
        Rule 55), and only for a move inside one GSTIN (ship's own
        gst_registrations answer -- a move across GSTINs goes on a T tax
        invoice). A request never shipped issued no challan;
      * a transfer tax invoice (T) in its DISPATCH month (tax_invoice_date,
        re-dated by a retried ship; else shipped_at) -- the date its B2B row
        files on -- and never in its mint month as well; a T no transfer
        holds: cancelled, in the month minted;
      * a credit / debit note (C / D): the month minted; a C serial the audit
        trail wrote off (CREDIT_NOTE_NUMBER_UNUSED) is cancelled.
    Invoices, challans and T numbers come from their documents (orders,
    stock_transfers), so a number minted before the register existed still
    counts; a filed CDNR note number the register never saw is a warning on
    ``issues`` (its serial cannot be placed in a run).
    A series is a prefix AND a serial width. A number of the series that
    belongs to another month, another shop's order, or no challan ends a
    run; a serial with no register row after a LOGGED number is CANCELLED --
    except a challan gap (another shop's number in the shared TRF-X- series,
    or a request that never shipped), a gap after a pre-register number
    (nothing says it was cancelled) and a gap over _MAX_GAP (warned), which
    end the run. A read failure raises (the doors 503).
    ponytail: a D serial counts as issued once logged -- no write-off path
    exists for it yet."""
    known: dict = {}  # series -> every number the shop was given
    minted: dict = {}  # series -> the numbers minted in the month
    for r in db.get_collection(DOCUMENT_LOG).find(
        {"store_id": store_id, "series": {"$in": list(_DOC_TYPES)}},
        {"_id": 0, "number": 1, "series": 1, "issued_at": 1},
    ):
        n, series = str(r.get("number") or ""), r.get("series")
        if not n:
            continue
        known.setdefault(series, set()).add(n)
        when = parse_event_utc_naive(r.get("issued_at"))
        if when is not None and from_dt <= when < to_dt:
            minted.setdefault(series, set()).add(n)
    state: dict = {s: {} for s in _DOC_TYPES}  # series -> {number: issued | cancelled}

    # The invoice tables' own rule (real_sale_match(gst_documents=True)): an
    # order cancelled behind a whole-invoice credit note is still filed.
    for o in db.get_collection("orders").find(
        {"store_id": store_id, "created_at": {"$gte": from_dt, "$lt": to_dt},
         "invoice_number": {"$nin": [None, ""]}},
        {"_id": 0, "invoice_number": 1, "status": 1, "status_before_void": 1,
         "advance_refund_status": 1, "advance_refund_credit_note": 1},
    ):
        if o.get("invoice_number"):
            state[""][o["invoice_number"]] = (
                "issued" if is_real_sale(o, gst_documents=True) else "cancelled"
            )
    unwritten = set(minted.get("", ()))
    for o in db.get_collection("orders").find(
        {"invoice_number": {"$in": sorted(unwritten)}}, {"_id": 0, "invoice_number": 1}
    ):
        unwritten.discard(o.get("invoice_number"))
    for n in unwritten:
        state[""][n] = "cancelled"

    # A T invoice files in its DISPATCH month -- the tax invoice's own date
    # (a retried ship re-dates it), the date its B2B row files on -- never in
    # the mint month as well. A number no transfer holds: its mint month.
    def _t_in_month(t) -> Optional[bool]:
        when = parse_event_utc_naive(t.get("tax_invoice_date") or t.get("shipped_at"))
        return None if when is None else from_dt <= when < to_dt

    def _t_state(t) -> str:
        dead = str(t.get("status") or "").lower() in ("cancelled", "rejected")
        return "cancelled" if dead else "issued"

    t_proj = {"_id": 0, "transfer_number": 1, "tax_invoice_number": 1, "tax_invoice_date": 1,
              "shipped_at": 1, "gst_registrations": 1, "status": 1}
    t_minted = minted.get("T", set())
    for n in t_minted:
        state["T"][n] = "cancelled"
    for t in db.get_collection("stock_transfers").find(
        {"tax_invoice_number": {"$in": sorted(t_minted)}}, t_proj
    ):
        if _t_in_month(t) is False:
            state["T"].pop(t["tax_invoice_number"], None)
        else:
            state["T"][t["tax_invoice_number"]] = _t_state(t)
    for t in db.get_collection("stock_transfers").find(
        {"from_location_id": store_id,
         "$or": instant_in_window_query("shipped_at", from_dt, to_dt)["$or"]
         + instant_in_window_query("tax_invoice_date", from_dt, to_dt)["$or"]},
        t_proj,
    ):
        tax_inv = t.get("tax_invoice_number")
        if tax_inv and tax_inv not in t_minted and _t_in_month(t):
            state["T"][tax_inv] = _t_state(t)
        shipped = parse_event_utc_naive(t.get("shipped_at"))
        crosses = (t.get("gst_registrations") or {}).get("crosses") or tax_inv
        if (t.get("transfer_number") and not crosses and shipped is not None
                and from_dt <= shipped < to_dt):
            state["TRF"][t["transfer_number"]] = "issued"
    for series in ("C", "D"):
        for n in minted.get(series, ()):
            state[series][n] = "issued"
    if minted.get("C"):
        for a in db.get_collection("audit_logs").find(
            {"action": "CREDIT_NOTE_NUMBER_UNUSED",
             "details.unused_credit_note_number": {"$in": sorted(minted["C"])}},
            {"_id": 0, "details": 1},
        ):
            n = str((a.get("details") or {}).get("unused_credit_note_number") or "")
            if n in state["C"]:
                state["C"][n] = "cancelled"
    filed = sorted(
        {str(c.get("refReference") or "") for c in cdnr if c.get("noteType") != "D"}
        - known.get("C", set()) - {""}
    )
    if filed and issues is not None:
        issues.append({
            "level": "warn",
            "invoice": filed[0],
            "issue": (
                f"Table 13: {len(filed)} credit note(s) filed in CDNR are not in the document "
                f"register ({filed[0]} .. {filed[-1]}) and are not counted under Credit Note "
                "-- the CA must add them to Table 13."
            ),
        })

    out: list = []
    for series in ("", "T", "D", "C", "TRF"):
        doc_num, doc_type = _DOC_TYPES[series]
        mine, given = state[series], set(known.get(series, ()))
        register = known.get(series, set())
        # One series = prefix AND serial width: an old-format number (the
        # pre-#1231 time-stamp fallback) never stretches a run.
        groups: dict = {}
        for n in mine:
            m = _SERIAL_RE.match(n)
            if m:
                groups.setdefault((m.group(1), len(m.group(2))), {})[int(m.group(2))] = n
        for prefix, width in sorted(groups):
            got = groups[(prefix, width)]
            serials = sorted(got)
            # A gap can read cancelled only inside the register's era (the
            # number before it logged, so every later mint is logged too),
            # never in a challan series, and only when it can be real.
            gaps: dict = {}
            for a, b in zip(serials, serials[1:]):
                if b - a - 1 > _MAX_GAP:
                    if issues is not None:
                        issues.append({"level": "warn", "invoice": got[a], "issue": (
                            f"Table 13: {b - a - 1} numbers missing between {got[a]} and "
                            f"{got[b]} -- not reported as cancelled; the CA must check them.")})
                elif b - a > 1 and series != "TRF" and got[a] in register:
                    gaps[a] = range(a + 1, b)
            held = given
            if series == "" and gaps:
                # A gap held by an order of another month, or of another shop
                # (re-routed: its invoice stays), ends the run.
                want = [f"{prefix}{x:0{width}d}" for g in gaps.values() for x in g]
                held = given | {o.get("invoice_number") for o in db.get_collection("orders").find(
                    {"invoice_number": {"$in": [n for n in want if n not in given]}},
                    {"_id": 0, "invoice_number": 1})}
            runs: list = [[]]
            for i, serial in enumerate(serials):
                if i and serial - serials[i - 1] > 1:
                    gap = gaps.get(serials[i - 1])
                    if gap is None:
                        runs.append([])
                    for x in gap or ():
                        if f"{prefix}{x:0{width}d}" in held:
                            runs.append([])
                        else:
                            runs[-1].append((x, "cancelled"))
                runs[-1].append((serial, mine[got[serial]]))
            for run in filter(None, runs):
                out.append({
                    "docNum": doc_num,
                    "docType": doc_type,
                    "from": f"{prefix}{run[0][0]:0{width}d}",
                    "to": f"{prefix}{run[-1][0]:0{width}d}",
                    "total": len(run),
                    "cancelled": sum(1 for _s, x in run if x != "issued"),
                })
    return out


def _hsn_rows(sale_lines: list, cn_lines: list, issues=None, part: str = "") -> list:
    """HSN summary rows per (HSN, rate): the sale lines' quantity, taxable and
    heads less the credit-note lines'. The portal takes no negative row: one
    its credit notes exceed is filed at 0 -- and, when ``issues`` is given (the
    filed Table 12 parts), SAID with the excess for the CA to carry forward."""
    by: dict = {}
    for sign, lines in ((1, sale_lines), (-1, cn_lines)):
        for ln in lines:
            hsn = str(ln.get("hsn") or "9004")
            rate = float(ln.get("gst_rate", 0) or 0)
            v = by.setdefault(
                f"{hsn}|{rate}",
                {"hsnCode": hsn, "gstRate": int(rate), "quantity": 0.0, "taxableValue": 0.0,
                 "cgst": 0.0, "sgst": 0.0, "igst": 0.0},
            )
            v["quantity"] += sign * float(ln.get("qty") or 0)
            for k, src in (("taxableValue", "taxable"), ("cgst", "cgst"), ("sgst", "sgst"), ("igst", "igst")):
                v[k] += sign * float(ln.get(src, 0) or 0)
    heads = ("taxableValue", "cgst", "sgst", "igst")
    if issues is not None:
        for v in by.values():
            over = {k: round(-v[k], 2) for k in heads if v[k] < -0.005}
            if over:
                issues.append(
                    {
                        "level": "error",
                        "invoice": f"HSN {v['hsnCode']} @ {v['gstRate']}% ({part})",
                        "issue": "Credit notes exceed this month's sales on this HSN row by "
                        + ", ".join(f"{k} Rs {a:,.2f}" for k, a in over.items())
                        + "; the row is filed at 0 -- carry the excess with the CA.",
                    }
                )
    # A service (SAC 99xx: a fitting 998599, an eye test 9993) has no pieces:
    # GSTN takes UQC "NA" with quantity 0 on it.
    return [
        {
            "hsnCode": v["hsnCode"],
            "gstRate": v["gstRate"],
            "uqc": "NA" if v["hsnCode"].startswith("99") else "PCS",
            "quantity": 0 if v["hsnCode"].startswith("99") else round(max(0, v["quantity"]), 3),
            **{k: round(max(0, v[k]), 2) for k in heads},
        }
        for v in by.values()
        if any(v[k] > 0 for k in heads)
    ]


def _gstr1_shop(store_id, current_user) -> str:
    """The shop whose GSTIN's GSTR-1 is asked for. The return files every
    shop on that GSTIN (_compute_gstr1), so a reader who cannot see every one
    of them is refused -- the GSTR-3B door's rule (gstr3b._gstin_return_shop).
    503 when the shops cannot be read."""
    from ...dependencies import user_store_scope
    from .gst_itc import _itc_store_scope

    shop = store_in_view(store_id, current_user, one_shop=True) or "store-001"
    db = _get_raw_db()
    if db is None:  # no database is not a nil return (gstr3b._gstin_return_shop)
        raise HTTPException(status_code=503, detail="GSTR-1 is unavailable; please retry.")
    try:
        filing = _itc_store_scope(db, shop)[2]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="GSTR-1 is unavailable; please retry.") from exc
    every, mine = user_store_scope(current_user)
    if not every and not all(s in mine for s in filing):
        raise HTTPException(
            status_code=403,
            detail=f"GSTR-1 covers {len(filing)} shops on this GSTIN - ask an admin.",
        )
    return shop


def _gstr1_return(month: str, shop: str, shape=None) -> dict:
    """GSTR-1 for the doors, by GSTR-3B's rule (gstr3b._gstin_return): a read
    or shaping failure is a 503 telling the user to retry -- an order no
    retry can file (UnfileableOrder) a 422 naming it -- never a report
    short of the orders, credit notes or returns it could not read, and never
    an empty or partial portal upload file. `shape` maps the report (the
    portal JSON door)."""
    try:
        data = _compute_gstr1(month, shop)
        return shape(data) if shape else data
    except HTTPException:
        raise
    except UnfileableOrder as exc:  # a retry cannot fix it: name the order
        raise HTTPException(status_code=422, detail=f"GSTR-1: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("[GSTR-1] %s %s could not be read", shop, month)
        raise HTTPException(
            status_code=503, detail="GSTR-1 figures could not be read; please retry."
        ) from exc


@router.get("/gstr1")
async def gstr1_report(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """GSTR-1 report (IMS internal shape). See _compute_gstr1 for details."""
    active_store = _gstr1_shop(store_id, current_user)
    return _gstr1_return(month, active_store)


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
    current_user: dict = Depends(require_roles(*AP_ROLES)),
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

    active_store = _gstr1_shop(store_id, current_user)
    period = _normalise_period(month, year)
    return _gstr1_return(
        period,
        active_store,
        shape=lambda data: to_gstr1_json(data, gstin=data.get("gstin", ""), period=period),
    )


