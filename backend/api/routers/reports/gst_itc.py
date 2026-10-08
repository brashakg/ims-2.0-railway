"""Vendor-bill ITC, stock-transfer and credit-note helpers for the GST returns."""

import re
from datetime import date

from ...services.ap_engine import iso_bill_date
from ...services.org_validation import itc_claimable, shop_gstins

# ============================================================================
# GST RETURNS - GSTR-3B (Summary Return)
# ============================================================================


_DEAD_BILL = ["CANCELLED", "cancelled", "VOID", "voided"]


def _itc_store_scope(db, active_store):
    """(entity_id, gstin, shops) of the store whose return is being filed.
    `gstin` is THE shop's GSTIN (org_validation.shop_gstin -- the one the
    bill door books its purchases on), '' when it has none; `shops` is every
    store whose GSTIN is the same (just this one when it has none) -- the
    shops whose receipts that one filing covers. Reading the raw stores.gstin
    here put a bill booked on a GSTIN-less shop's company number on no return
    at all. Fail-soft."""
    try:
        gstins = shop_gstins(db)
        row = db["stores"].find_one({"store_id": active_store}, {"entity_id": 1})
    except Exception:
        return None, "", [active_store]
    gstin = gstins.get(active_store, "")
    shops = {active_store}
    if gstin:
        shops.update(sid for sid, g in gstins.items() if g == gstin)
    return (row or {}).get("entity_id"), gstin, sorted(shops)


def _month_days(year, mon, last_day) -> list:
    """THE days of a GST return's month: each day of the month that THE
    bill-date rule every door books through (ap_engine.iso_bill_date: a real
    calendar day from the start of GST to today in IST) accepts. A bill is in
    the month when the first ten characters of its date are one of them (a
    transfer mirror stores a full IST timestamp). Placement (_itc_month) and
    the Cross-Check's unplaced count read this one list, so a stored non-day
    like '2026-04-31' is on no month's return and flagged in every month --
    a string range placed it on April's."""
    days = []
    for n in range(1, last_day + 1):
        try:
            days.append(iso_bill_date(date(year, mon, n).isoformat()))
        except ValueError:
            pass
    return days


def _itc_month(year, mon, last_day) -> list:
    """The bills dated in the month (_month_days) on invoice_date or bill_date."""
    day = {"$in": [re.compile("^" + d) for d in _month_days(year, mon, last_day)]}
    return [{"invoice_date": day}, {"bill_date": day}]


def _dated(value) -> bool:
    """Is a bill dated `value` on SOME month's return? The same rule as
    _month_days, read on the day part: '' / '09/05/2026' / '2026-04-31' (no
    such day) / '2062-05-09' / a non-string are on none."""
    if not isinstance(value, str):
        return False
    try:
        iso_bill_date(value[:10])
    except ValueError:
        return False
    return True


def _placement(shops, entity_id, store_gstin, year, mon, last_day) -> dict:
    """THE placement of a vendor bill on ONE GSTIN's GSTR-3B -- the single
    query Table 4 (input credit, _itc_match) and Table 3.1(d) (reverse charge,
    _rcm_from_vendor_bills) are both built from, so a reverse-charge bill's
    liability and its credit always land on the same return.

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
        whichever shop it meets first. A shop with NO GSTIN keeps no mirror:
        it files no return, so that credit is left off every return and
        _itc_unplaced flags it;
      * a legacy purchase bill that names no GSTIN stays company-wide.
    Always scoped to the store's company (recipient_entity_id) when it has one,
    and to live bills dated in the month.
    """
    # A transfer mirror this return does NOT keep. source_transfer_id uses
    # {$exists,$ne None} to stay aligned with the sender-side collector
    # (_transfer_outward_bills). A shop with no GSTIN files no return, so it
    # keeps none: placed on to_store_id alone, a mirror into a shop its
    # company holds no registration for read as filed (Cross-Check ITC 50,
    # 'left off' MATCH) while the company's real return claimed nothing.
    other_mirror: dict = {"source_transfer_id": {"$exists": True, "$ne": None}}
    if store_gstin:
        other_mirror["$nor"] = [
            {"recipient_gstin": store_gstin},
            {"recipient_gstin": {"$in": ["", None]}, "to_store_id": {"$in": list(shops)}},
        ]
    this_gstin = ["", None] + ([store_gstin] if store_gstin else [])
    vb_match: dict = {
        "status": {"$nin": _DEAD_BILL},
        "$or": _itc_month(year, mon, last_day),
        "$nor": [
            other_mirror,
            # A purchase bill received on ANOTHER of our GSTINs.
            {"source_transfer_id": None, "recipient_gstin": {"$nin": this_gstin}},
        ],
    }
    if entity_id:
        vb_match["recipient_entity_id"] = entity_id
    return vb_match


def _itc_match(shops, entity_id, store_gstin, year, mon, last_day) -> dict:
    """Table 4: the bills _placement puts on this GSTIN's return that carry
    input credit -- every ITC read (the return, its GSTIN slice, the
    Cross-Check's count of credit left off every return) is built from it."""
    return {
        **_placement(shops, entity_id, store_gstin, year, mon, last_day),
        "itc_eligible": {"$ne": False},
    }


def _gstin_bound(shops, store_gstin) -> dict:
    """The GSTIN-BOUND part of a return (R1): bills received on this GSTIN,
    plus GSTIN-less transfer mirrors received at any shop carrying it. The
    rest of a placed figure is company-wide (legacy bills naming no GSTIN),
    so the Cross-Check counts this part once per GSTIN, the rest once per
    company."""
    bound: list = [
        {
            "source_transfer_id": {"$exists": True, "$ne": None},
            "recipient_gstin": {"$in": ["", None]},
            "to_store_id": {"$in": list(shops)},
        }
    ]
    if store_gstin:
        bound.insert(0, {"recipient_gstin": store_gstin})
    return {"$or": bound}


def _sum_heads(db, match):
    """(igst, cgst, sgst, taxable) summed from the bills' own stored heads."""
    pipeline = [
        {"$match": match},
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
        return tuple(
            float(a.get(k, 0.0) or 0.0) for k in ("igst", "cgst", "sgst", "taxable")
        )
    return 0.0, 0.0, 0.0, 0.0


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
        return _sum_heads(
            db, _itc_match(shops, entity_id, store_gstin, year, mon, last_day)
        )[:3]
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _itc_gstin_from_vendor_bills(db, active_store, year, mon, last_day):
    """R1: the GSTIN-BOUND slice of Table-4 ITC -- every bill received on this
    store's GSTIN (purchase bills and transfer mirrors alike), plus transfer
    mirrors with no GSTIN received at any shop carrying that GSTIN (nothing
    when it has none). Returns (igst, cgst, sgst); fail-soft ->
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
        match = _itc_match(shops, entity_id, store_gstin, year, mon, last_day)
        return _sum_heads(db, {"$and": [match, _gstin_bound(shops, store_gstin)]})[:3]
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _itc_unplaced(db, year, mon, last_day, entity_id=None) -> dict:
    """Booked input credit that NO GSTIN's GSTR-3B counts this month: a bill
    with no company (recipient_entity_id null -- every screen bill before F40),
    a bill whose GSTIN is no shop's, a bill with tax but no stored heads
    (Table 4 sums the heads), or a bill no month can place (not _dated: listed
    in every month, since it is on none). Placement is _itc_match run for
    every shop that has a company, on THE shop's GSTIN (org_validation.
    shop_gstins, the same answer GSTR-3B's scope reads), so this cannot
    disagree with the returns. A shop with no company places nothing: its
    GSTR-3B has no company filter, so it would mark every company's
    GSTIN-less bill as placed while the Cross-Check counts none of its credit.
    Scoped to `entity_id` plus the company-less bills (they belong to nobody,
    so every view shows them).

    Also `unregistered`: credit that IS on a return although the bill names no
    supplier GSTIN and is not reverse charge -- an unregistered supplier's tax
    never reaches GSTR-2B, so that credit cannot be claimed. The bill's stored
    vendor_gstin judges it, never the vendor master: the head was decided
    without the supplier's state, so a GSTIN added to the supplier later
    leaves the bill's head wrong.

    Returns {count, tax, bill_numbers, unregistered: {count, tax,
    bill_numbers, transfer_bill_numbers}, denied_transfers: [{bill_number,
    from_shop, tax}]} (denied_transfers = mirrors given NO credit because the
    sender has no valid GSTIN; unregistered's last lists the stock-transfer
    mirror bills among them -- the system made those, no one booked them). A read failure returns {failed: True} -- never zeros, which
    the Cross-Check would show as a green row."""
    out = {"count": 0, "tax": 0.0, "bill_numbers": []}
    denied: dict = {"transfers": []}
    unreg = {"count": 0, "tax": 0.0, "bill_numbers": [], "transfer_bill_numbers": []}
    if db is None:
        return {**out, "unregistered": unreg, "denied_transfers": []}

    def _add(acc, bill, tax):
        acc["count"] += 1
        acc["tax"] = round(acc["tax"] + tax, 2)
        acc["bill_numbers"].append(bill.get("bill_number") or bill.get("bill_id"))

    days = set(_month_days(year, mon, last_day))
    try:
        gstins = shop_gstins(db)
        by_gstin: dict = {}
        for sid, g in gstins.items():
            if g:
                by_gstin.setdefault(g, []).append(sid)
        placed = set()
        filings = set()
        for st in db["stores"].find({}, {"_id": 0, "store_id": 1, "entity_id": 1}):
            eid = st.get("entity_id")
            if not eid:
                continue
            g = gstins.get(st.get("store_id"), "")
            filing = (eid, g or st.get("store_id"))
            if filing in filings:
                continue  # one GSTIN, one filing: same placement
            filings.add(filing)
            match = _itc_match(
                by_gstin.get(g) or [st.get("store_id")], eid, g, year, mon, last_day
            )
            placed.update(
                b.get("bill_id")
                for b in db["vendor_bills"].find(match, {"_id": 0, "bill_id": 1})
            )
        q: dict = {"status": {"$nin": _DEAD_BILL}, "itc_eligible": {"$ne": False}}
        if entity_id:
            q["recipient_entity_id"] = {"$in": [entity_id, None]}
        # ponytail: reads every live bill of the scope -- an undated bill is
        # in no month's window, yet is listed in every month; add a
        # "this month or undated" prefilter if this grows slow.
        for b in db["vendor_bills"].find(q, {"_id": 0}):
            dates = (b.get("invoice_date"), b.get("bill_date"))
            in_month = any(isinstance(d, str) and d[:10] in days for d in dates)
            if not in_month and any(_dated(d) for d in dates):
                continue  # another month's return places it
            tax = round(float(b.get("tax_amount") or 0), 2)
            if tax <= 0:
                continue
            has_heads = any(k in b for k in ("cgst_total", "sgst_total", "igst_total"))
            if b.get("bill_id") not in placed or not has_heads:
                _add(out, b, tax)
                continue
            # The bill's own supplier GSTIN decided its head; the vendor
            # master's current one did not, so it never clears the bill.
            if not itc_claimable(b.get("vendor_gstin"), b.get("reverse_charge")):
                _add(unreg, b, tax)
                if b.get("source_transfer_id"):
                    unreg["transfer_bill_numbers"].append(
                        b.get("bill_number") or b.get("bill_id")
                    )
        # Transfer mirrors whose credit IS denied (the sending shop has no valid
        # GSTIN): the right verdict, but the filer must be told, so they are
        # listed for the Cross-Check. Same scope and month window as above.
        dq: dict = {
            "status": {"$nin": _DEAD_BILL},
            "itc_eligible": False,
            "source_transfer_id": {"$exists": True, "$ne": None},
        }
        if entity_id:
            dq["recipient_entity_id"] = {"$in": [entity_id, None]}
        denied["transfers"] = []
        for b in db["vendor_bills"].find(dq, {"_id": 0}):
            dates = (b.get("invoice_date"), b.get("bill_date"))
            in_month = any(isinstance(d, str) and d[:10] in days for d in dates)
            if not in_month and any(_dated(d) for d in dates):
                continue
            tax = round(float(b.get("tax_amount") or 0), 2)
            if tax <= 0 or itc_claimable(b.get("vendor_gstin"), b.get("reverse_charge")):
                continue
            denied["transfers"].append(
                {
                    "bill_number": b.get("bill_number") or b.get("bill_id"),
                    "from_shop": b.get("vendor_name") or b.get("vendor_id") or "",
                    "tax": tax,
                }
            )
    except Exception:
        return {"count": 0, "tax": 0.0, "bill_numbers": [], "failed": True}
    return {**out, "unregistered": unreg, "denied_transfers": denied["transfers"]}


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
        query = {
            "source_transfer_id": {"$exists": True, "$ne": None},
            "from_store_id": active_store,
            "status": {"$nin": _DEAD_BILL},
            "$or": _itc_month(year, mon, last_day),
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
    """The returns doc a credit-note ledger row was minted for -- else the
    row itself when its door stamped the order it reverses (the SUPERADMIN
    post-invoice credit note, which has no returns doc) -- or None.

    The row's ref/reason carry the RET- id the note was issued against -- the
    same tokens both dedup scans already read. ONE lookup rule shared by the
    GSTR-1 CDNR pass and the GSTR-3B credit-note leg, so the two returns can
    never attribute the same note differently -- and every check on the
    note's parent (its store, its tax head, its held sale) sees every door's
    note. Fail-soft -> None (a manual note names no order and stays
    attributed where booked).
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
    return row if row.get("order_id") else None


def _db_store_finder(db):
    """``find_store(store_id)`` over the raw db, for the seller check."""

    def find(sid):
        return db.get_collection("stores").find_one({"store_id": sid})

    return find


def _order_held_off_returns(db, order) -> dict:
    """The seller-check problem that keeps an order OFF the GST returns (None
    when it files): the seller hold its booking put on it still stands
    (online_fulfillment_route.seller_problem -- the invoice door, the
    challan, the e-invoice and GSTR-1 refuse on the same one; GSTR-3B and
    Tally ask the SAME question, so the returns of one GSTIN never disagree
    on a held order). Never re-judged on today's shop records: an order
    never held, or released, files under its booked invoice date even if a
    shop's GSTIN or state is edited later -- Re-map and clear-hold never
    change it. None for an order never routed (POS, a historical import)."""
    from ...services.online_fulfillment_route import stored_seller_problem

    return stored_seller_problem(order, _db_store_finder(db))


def _cn_parent_held(db, ret_doc, cache) -> bool:
    """True when a credit note's (return doc's) PARENT order is held off the
    returns (_order_held_off_returns): its sale was never filed, so its
    credit note must not be either -- reversing output tax on a supply never
    declared (and, for a B2B buyer, uploading the CDNR row). ONE answer for
    GSTR-1's ledger + in-store passes and GSTR-3B's two legs. ``cache`` is
    keyed by order_id. Fail-soft -> False (the note files, as before)."""
    oid = str((ret_doc or {}).get("order_id") or "")
    if not oid:
        return False
    if oid not in cache:
        try:
            order = db.get_collection("orders").find_one({"order_id": oid})
            cache[oid] = bool(_order_held_off_returns(db, order))
        except Exception:  # noqa: BLE001
            cache[oid] = False
    return cache[oid]


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


