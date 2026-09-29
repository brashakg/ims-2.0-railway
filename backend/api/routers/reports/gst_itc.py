"""Vendor-bill ITC, stock-transfer and credit-note helpers for the GST returns."""

import re

# ============================================================================
# GST RETURNS - GSTR-3B (Summary Return)
# ============================================================================


_DEAD_BILL = ["CANCELLED", "cancelled", "VOID", "voided"]


def _itc_store_scope(db, active_store):
    """(entity_id, gstin, shops) of the store whose return is being filed:
    `shops` is every store that carries the same GSTIN (just this one when it
    has none) -- the shops whose receipts that one filing covers. Fail-soft."""
    try:
        row = db["stores"].find_one(
            {"store_id": active_store}, {"entity_id": 1, "gstin": 1}
        )
        gstin = str((row or {}).get("gstin", "") or "").strip()
        shops = {active_store}
        if gstin:
            shops.update(
                s.get("store_id")
                for s in db["stores"].find({"gstin": gstin}, {"store_id": 1})
            )
    except Exception:
        return None, "", [active_store]
    return (row or {}).get("entity_id"), gstin, sorted(x for x in shops if x)


def _itc_month(year, mon, last_day) -> list:
    """The string-date month window (invoice_date / bill_date are ISO strings)."""
    month_lo = f"{year:04d}-{mon:02d}-01"
    month_hi = f"{year:04d}-{mon:02d}-{last_day:02d}T23:59:59"
    return [
        {"invoice_date": {"$gte": month_lo, "$lte": month_hi}},
        {"bill_date": {"$gte": month_lo, "$lte": month_hi}},
    ]


# A bill that no _itc_month window can place: neither date starts YYYY-MM-DD
# ('' / '09/05/2026' / missing -- booked before every door validated it with
# ap_engine.iso_bill_date). It is on no month's return, so the check below
# reports it in every month until the bill is corrected.
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
_UNDATED = {"invoice_date": {"$not": _ISO_DAY}, "bill_date": {"$not": _ISO_DAY}}


def _itc_match(shops, entity_id, store_gstin, year, mon, last_day) -> dict:
    """THE placement of input credit on ONE GSTIN's GSTR-3B Table 4 -- the
    single query every ITC read (the return, its GSTIN slice, the Cross-Check's
    count of credit left off every return) is built from.

    A bill counts on the return of the GSTIN it was RECEIVED on: one GSTIN, one
    filing, so every store of a multi-store GSTIN produces the SAME Table 4 and
    no credit is claimed by two registrations. Concretely:
      * a bill that names our GSTIN (recipient_gstin) counts only on that
        GSTIN's return. A company holding two registrations (Jharkhand +
        Maharashtra) used to see every one of its bills on BOTH returns -- a
        purchase bill was scoped by company alone;
      * a stock-transfer mirror with no recipient GSTIN counts on the return
        of its receiving shop's GSTIN (to_store_id in `shops`, every store
        carrying that GSTIN) -- the sender must never claim ITC on its own
        outward supply (NEW-GST-TRANSFER-OUTWARD), and two shops sharing one
        GSTIN (a shop stamped with its company's other-state number) print the
        same Table 4, so the Cross-Check's once-per-GSTIN count is the same
        whichever shop it meets first;
      * a legacy purchase bill that names no GSTIN stays company-wide.
    Always scoped to the store's company (recipient_entity_id) when it has one,
    and to live, ITC-eligible bills dated in the month.
    """
    transfer_keep: list = []
    if store_gstin:
        transfer_keep.append({"recipient_gstin": store_gstin})
    transfer_keep.append(
        {"recipient_gstin": {"$in": ["", None]}, "to_store_id": {"$in": list(shops)}}
    )
    this_gstin = ["", None] + ([store_gstin] if store_gstin else [])
    vb_match: dict = {
        "status": {"$nin": _DEAD_BILL},
        "itc_eligible": {"$ne": False},
        "$or": _itc_month(year, mon, last_day),
        "$nor": [
            # A transfer mirror meeting NEITHER keep-condition. source_transfer_id
            # uses {$exists,$ne None} to stay aligned with the sender-side
            # collector (_transfer_outward_bills).
            {
                "source_transfer_id": {"$exists": True, "$ne": None},
                "$nor": transfer_keep,
            },
            # A purchase bill received on ANOTHER of our GSTINs.
            {"source_transfer_id": None, "recipient_gstin": {"$nin": this_gstin}},
        ],
    }
    if entity_id:
        vb_match["recipient_entity_id"] = entity_id
    return vb_match


def _sum_itc(db, match):
    """(igst, cgst, sgst) summed from the bills' own stored heads."""
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "igst": {"$sum": "$igst_total"},
                "cgst": {"$sum": "$cgst_total"},
                "sgst": {"$sum": "$sgst_total"},
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
        )
    return 0.0, 0.0, 0.0


def _itc_from_vendor_bills(db, active_store, year, mon, last_day):
    """BUG-138: ITC available for a month from recorded PURCHASE INVOICES
    (vendor_bills cgst/sgst/igst_total), placed on the store's GSTIN by
    _itc_match. Returns (igst, cgst, sgst). The old code summed the `grns`
    collection -- quantity-only with NO tax fields -- so ITC was always 0 and
    the business over-paid GST. Fail-soft -> (0.0, 0.0, 0.0)."""
    if db is None:
        return 0.0, 0.0, 0.0
    try:
        entity_id, store_gstin, shops = _itc_store_scope(db, active_store)
        return _sum_itc(
            db, _itc_match(shops, entity_id, store_gstin, year, mon, last_day)
        )
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _itc_gstin_from_vendor_bills(db, active_store, year, mon, last_day):
    """R1: the GSTIN-BOUND slice of Table-4 ITC -- every bill received on this
    store's GSTIN (purchase bills and transfer mirrors alike), plus transfer
    mirrors with no GSTIN received at any shop carrying that GSTIN (at this
    shop alone when it has none). Returns (igst, cgst, sgst); fail-soft ->
    zeros.

    The same _itc_match AND-ed with the GSTIN binding, so (this) + (the
    company-wide remainder: legacy bills naming no GSTIN) ==
    _itc_from_vendor_bills by construction. Sibling stores of one company with
    DIFFERENT GSTINs return DIFFERENT slices, so gst_crosscheck.aggregate_gstr3b
    dedupes this slice once per GSTIN and the remainder once per company: the
    company figure is independent of store enumeration order and never counts
    one bill on two registrations."""
    if db is None:
        return 0.0, 0.0, 0.0
    try:
        entity_id, store_gstin, shops = _itc_store_scope(db, active_store)
        bound: list = [
            {
                "source_transfer_id": {"$exists": True, "$ne": None},
                "recipient_gstin": {"$in": ["", None]},
                "to_store_id": {"$in": shops},
            }
        ]
        if store_gstin:
            bound.insert(0, {"recipient_gstin": store_gstin})
        match = _itc_match(shops, entity_id, store_gstin, year, mon, last_day)
        return _sum_itc(db, {"$and": [match, {"$or": bound}]})
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _itc_unplaced(db, year, mon, last_day, entity_id=None) -> dict:
    """Booked input credit that NO GSTIN's GSTR-3B counts this month: a bill
    with no company (recipient_entity_id null -- every screen bill before F40),
    a bill whose GSTIN is no shop's, a bill with tax but no stored heads
    (Table 4 sums the heads), or a bill no month can place (_UNDATED: listed
    in every month, since it is on none). Placement is _itc_match run for
    every shop that has a company (the Cross-Check never counts a company-less shop's credit),
    so this cannot disagree with the returns. Scoped to `entity_id` plus the
    company-less bills (they belong to nobody, so every view shows them).

    Also `unregistered`: credit that IS on a return although the supplier has
    no GSTIN (neither on the bill nor on the vendor) and the bill is not
    reverse charge -- an unregistered supplier's tax never reaches GSTR-2B, so
    that credit cannot be claimed.

    Returns {count, tax, bill_numbers, unregistered: {count, tax,
    bill_numbers}}. A read failure returns {failed: True} -- never zeros, which
    the Cross-Check would show as a green row."""
    out = {"count": 0, "tax": 0.0, "bill_numbers": []}
    unreg = {"count": 0, "tax": 0.0, "bill_numbers": []}
    if db is None:
        return {**out, "unregistered": unreg}

    def _add(acc, bill, tax):
        acc["count"] += 1
        acc["tax"] = round(acc["tax"] + tax, 2)
        acc["bill_numbers"].append(bill.get("bill_number") or bill.get("bill_id"))

    try:
        stores = list(
            db["stores"].find({}, {"_id": 0, "store_id": 1, "entity_id": 1, "gstin": 1})
        )
        by_gstin: dict = {}
        for st in stores:
            g = str(st.get("gstin") or "").strip()
            if g:
                by_gstin.setdefault(g, []).append(st.get("store_id"))
        placed = set()
        for st in stores:
            if not st.get("entity_id"):
                continue
            g = str(st.get("gstin") or "").strip()
            match = _itc_match(
                by_gstin.get(g) or [st.get("store_id")],
                st.get("entity_id"),
                g,
                year,
                mon,
                last_day,
            )
            placed.update(
                b.get("bill_id")
                for b in db["vendor_bills"].find(match, {"_id": 0, "bill_id": 1})
            )
        q: dict = {
            "status": {"$nin": _DEAD_BILL},
            "itc_eligible": {"$ne": False},
            "$or": _itc_month(year, mon, last_day) + [_UNDATED],
        }
        if entity_id:
            q["recipient_entity_id"] = {"$in": [entity_id, None]}
        vendor_gstin: dict = {}
        for b in db["vendor_bills"].find(q, {"_id": 0}):
            tax = round(float(b.get("tax_amount") or 0), 2)
            if tax <= 0:
                continue
            has_heads = any(k in b for k in ("cgst_total", "sgst_total", "igst_total"))
            if b.get("bill_id") not in placed or not has_heads:
                _add(out, b, tax)
                continue
            if b.get("reverse_charge") or str(b.get("vendor_gstin") or "").strip():
                continue
            vid = b.get("vendor_id")
            if vid not in vendor_gstin:
                v = db["vendors"].find_one({"vendor_id": vid}, {"_id": 0, "gstin": 1})
                vendor_gstin[vid] = str((v or {}).get("gstin") or "").strip()
            if not vendor_gstin[vid]:
                _add(unreg, b, tax)
    except Exception:
        return {"count": 0, "tax": 0.0, "bill_numbers": [], "failed": True}
    return {**out, "unregistered": unreg}


def _transfer_outward_bills(db, active_store, year, mon, last_day):
    """NEW-GST-TRANSFER-OUTWARD (GAP A): the SENDING side of an inter-GSTIN
    stock transfer (Schedule I deemed supply between distinct persons).

    transfers._book_mirror_purchase writes ONE vendor_bills doc per cross-GSTIN
    transfer -- the RECEIVING entity's ITC record. That SAME doc is the sending
    GSTIN's outward tax invoice, so the sender's GSTR-1 B2B rows and GSTR-3B
    3.1(a) totals are read from it here, keyed by from_store_id == the sending
    store. Reading one shared doc for both sides makes the two filings
    reconcile BY CONSTRUCTION: sender outward IGST == receiver ITC claim,
    paisa-exact.

    Same string-date month window as _itc_from_vendor_bills, so the sender
    reports the outward supply in the SAME period the receiver claims the ITC.
    Only forward-charge deemed supply lives here -- RCM inward supplies are a
    separate flow (_rcm_from_vendor_bills; vendor_bills.reverse_charge=True).
    Fail-soft -> [].
    """
    if db is None:
        return []
    try:
        month_lo = f"{year:04d}-{mon:02d}-01"
        month_hi = f"{year:04d}-{mon:02d}-{last_day:02d}T23:59:59"
        query = {
            "source_transfer_id": {"$exists": True, "$ne": None},
            "from_store_id": active_store,
            "status": {"$nin": ["CANCELLED", "cancelled", "VOID", "voided"]},
            "$or": [
                {"invoice_date": {"$gte": month_lo, "$lte": month_hi}},
                {"bill_date": {"$gte": month_lo, "$lte": month_hi}},
            ],
        }
        return [b for b in db["vendor_bills"].find(query) if isinstance(b, dict)]
    except Exception:
        return []


def _transfer_b2b_rows(bills):
    """Map sender-side transfer mirror bills to (GSTR-1 B2B rows, HSN lines).

    Pure -- no I/O. Each bill becomes one B2B invoice row (the recipient is our
    own sister GSTIN, i.e. a registered person -> B2B section 4A) flagged
    deemedSupply=True so the UI/CA can tell it from a customer sale and the
    HSN-summary builder knows to use the PER-LINE detail instead of the
    row-level dominant HSN (a transfer can mix 5% frames with 18% sunglasses).

    Returns (rows, hsn_lines) where hsn_lines is a flat list of
    {hsn, gst_rate, taxable, cgst, sgst, igst} dicts across all bills. Bills
    without per-line detail (legacy) contribute one header-level HSN line.

    Each row also carries `rateLines`: the bill's lines AGGREGATED PER GST
    RATE. The portal's B2B invoice entry is a list of itm_det blocks, ONE PER
    RATE -- a single blended block (e.g. rt=5 with the tax of a 5%+18% mix)
    fails the offline tool's txval*rt==iamt validation. gstn_export._build_b2b
    emits one itm per rateLines entry; rows without rateLines (normal order
    rows) keep the single-item path.

    Zero-value bills (taxable <= 0 and tax <= 0, e.g. a cost-less transfer)
    are SKIPPED entirely -- an all-zero rt=0 invoice is portal noise that the
    offline tool rejects, and it contributes nothing to any total.
    """
    rows = []
    hsn_lines = []
    for b in bills:
        if not isinstance(b, dict):
            continue
        taxable = float(b.get("taxable_amount", b.get("taxable_total", 0)) or 0)
        cgst = float(b.get("cgst_total", 0) or 0)
        sgst = float(b.get("sgst_total", 0) or 0)
        igst = float(b.get("igst_total", 0) or 0)
        tax = float(b.get("tax_amount", 0) or 0) or round(cgst + sgst + igst, 2)
        if taxable <= 0 and tax <= 0:
            # Zero-value bill: nothing to report outward (see docstring).
            continue
        lines = [ln for ln in (b.get("lines") or []) if isinstance(ln, dict)]

        recipient_gstin = str(b.get("recipient_gstin", "") or "").strip()
        recipient_state = str(b.get("supply_place_recipient", "") or "").strip()
        if not recipient_state and len(recipient_gstin) >= 2 and recipient_gstin[:2].isdigit():
            recipient_state = recipient_gstin[:2]

        first = lines[0] if lines else {}
        try:
            dominant_rate = float(first.get("gst_rate"))
        except (TypeError, ValueError):
            # Legacy header-only bill: derive the effective rate from the money.
            dominant_rate = round(tax / taxable * 100.0, 2) if taxable else 0.0

        # Per-rate aggregation for the portal itm_det blocks (one per rate).
        rate_map: dict = {}
        for ln in lines:
            r = float(ln.get("gst_rate", 0) or 0)
            bucket = rate_map.setdefault(
                r, {"rate": r, "taxable": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0}
            )
            bucket["taxable"] = round(bucket["taxable"] + float(ln.get("taxable", 0) or 0), 2)
            bucket["cgst"] = round(bucket["cgst"] + float(ln.get("cgst", 0) or 0), 2)
            bucket["sgst"] = round(bucket["sgst"] + float(ln.get("sgst", 0) or 0), 2)
            bucket["igst"] = round(bucket["igst"] + float(ln.get("igst", 0) or 0), 2)
        rate_lines = [rate_map[r] for r in sorted(rate_map)]

        raw_date = str(b.get("invoice_date") or b.get("bill_date") or "")
        rows.append(
            {
                "invoiceNumber": str(
                    b.get("invoice_number") or b.get("bill_number") or ""
                ),
                "invoiceDate": raw_date[:10],
                "customerName": str(
                    b.get("recipient_name") or b.get("entity_id") or ""
                ),
                "customerGSTIN": recipient_gstin,
                "customerState": recipient_state,
                "placeOfSupply": recipient_state or "Unknown",
                "invoiceValue": round(
                    float(b.get("total_amount", 0) or 0) or (taxable + tax), 2
                ),
                "taxableValue": round(taxable, 2),
                "cgst": round(cgst, 2),
                "sgst": round(sgst, 2),
                "igst": round(igst, 2),
                "totalTax": round(tax, 2),
                "hsnCode": str(first.get("hsn") or "9004"),
                "gstRate": dominant_rate,
                # Per-rate tax blocks for the portal export (one itm per rate;
                # empty for legacy header-only bills -> single-item path).
                "rateLines": rate_lines,
                # Markers: deemed supply on an inter-GSTIN stock transfer.
                "deemedSupply": True,
                "documentType": "STOCK_TRANSFER",
                "sourceTransferId": b.get("source_transfer_id"),
            }
        )

        if lines:
            for ln in lines:
                hsn_lines.append(
                    {
                        "hsn": str(ln.get("hsn") or "9004"),
                        "gst_rate": float(ln.get("gst_rate", 0) or 0),
                        "taxable": float(ln.get("taxable", 0) or 0),
                        "cgst": float(ln.get("cgst", 0) or 0),
                        "sgst": float(ln.get("sgst", 0) or 0),
                        "igst": float(ln.get("igst", 0) or 0),
                    }
                )
        else:
            hsn_lines.append(
                {
                    "hsn": "9004",
                    "gst_rate": dominant_rate,
                    "taxable": round(taxable, 2),
                    "cgst": round(cgst, 2),
                    "sgst": round(sgst, 2),
                    "igst": round(igst, 2),
                }
            )
    return rows, hsn_lines


def _return_interstate_flag(db, ret, store_state, cache, fallback_state=""):
    """Is this refund an INTER-state reversal? One answer, used by both returns.

    A refund must reverse the SAME head the sale was filed under, so the parent
    order's own `interstate` stamp is the answer whenever it exists. Only when
    the order carries no stamp do we derive it from the customer's state.

    ONE implementation because there are two consumers -- GSTR-3B Table 3.1(a)
    and the GSTR-1 CDNR rows -- and they disagreed: 3.1(a) preferred the order
    stamp and CDNR always re-derived from the customer. An online buyer record
    is minted stateless, so the same refund reversed IGST in 3B and CGST/SGST
    in GSTR-1, and the two returns the accountant types could not reconcile.
    `cache` is a per-report dict keyed by order_id.
    """
    oid = str(ret.get("order_id") or "")
    if oid and oid in cache:
        return cache[oid]
    flag = None
    if oid:
        try:
            po = db.get_collection("orders").find_one(
                {"order_id": oid}, {"interstate": 1}
            ) or {}
            if isinstance(po.get("interstate"), bool):
                flag = po["interstate"]
        except Exception:  # noqa: BLE001
            flag = None
    if flag is None:
        cs = fallback_state or ""
        if not cs:
            try:
                cu = db.get_collection("customers").find_one(
                    {"customer_id": str(ret.get("customer_id") or "")}, {"state": 1}
                ) or {}
                cs = str(cu.get("state") or "")
            except Exception:  # noqa: BLE001
                cs = ""
        flag = bool(
            store_state and cs and store_state.strip().lower() != cs.strip().lower()
        )
    if oid:
        cache[oid] = flag
    return flag


def _ledger_row_return_doc(db, row):
    """The returns doc a credit-note ledger row was minted for, or None.

    The row's ref/reason carry the RET- id the note was issued against -- the
    same tokens both dedup scans already read. ONE lookup rule shared by the
    GSTR-1 CDNR pass and the GSTR-3B credit-note leg, so the two returns can
    never attribute the same note differently. Fail-soft -> None (manual /
    superadmin notes carry no RET- ref and stay attributed where booked).
    """
    for f in ("ref", "reason"):
        for tok in str(row.get(f) or "").replace(",", " ").split():
            if not tok.startswith("RET-"):
                continue
            try:
                ret = db.get_collection("returns").find_one(
                    {"return_id": tok.strip(".:;")}
                )
            except Exception:  # noqa: BLE001
                ret = None
            if isinstance(ret, dict):
                return ret
    return None


def _cn_foreign_store(ret_doc, active_store) -> bool:
    """True when a ledger row's return belongs to a DIFFERENT store's GSTIN.

    Legacy rows were booked under the CASHIER's store while the return doc
    carries the ORDER's store, so the store-scoped dedup missed them and one
    refund reversed output tax under two GSTINs. The sale store's report owns
    the reversal (its returns leg counts the return doc); the booking store
    must skip the row. New rows are booked under the order's store, so this
    only bites the legacy mismatches -- no stored row is ever rewritten.
    """
    if not isinstance(ret_doc, dict):
        return False
    ret_store = str(ret_doc.get("store_id") or "")
    return bool(ret_store) and ret_store != str(active_store)


def _cn_bucket_rate(explicit_rate, tax, taxable) -> int:
    """GST rate for a credit-note row WITHOUT fabricating one.

    The stamped rate wins. A legacy row with no usable stamp derives the rate
    from its own tax/taxable (the note's arithmetic truth). When nothing can
    be derived the row files at 0 -- the old blanket 18% default subtracted a
    5% optical credit note from the 18% HSN bucket, understating declared 18%
    turnover (and overstating 5%).
    """
    try:
        r = float(explicit_rate)
    except (TypeError, ValueError):
        r = 0.0
    if r > 0:
        return int(round(r))
    try:
        t = float(tax or 0.0)
        tv = float(taxable or 0.0)
    except (TypeError, ValueError):
        return 0
    if t > 0 and tv > 0:
        return int(round(t / tv * 100.0))
    return 0


