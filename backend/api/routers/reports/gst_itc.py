"""Vendor-bill ITC, stock-transfer and credit-note helpers for the GST returns."""


# ============================================================================
# GST RETURNS - GSTR-3B (Summary Return)
# ============================================================================


def _itc_from_vendor_bills(db, active_store, year, mon, last_day):
    """BUG-138: ITC available for a month from recorded PURCHASE INVOICES
    (vendor_bills cgst/sgst/igst_total), scoped to the store's entity. Returns
    (igst, cgst, sgst). The old code summed the `grns` collection -- quantity-only
    with NO tax fields -- so ITC was always 0 and the business over-paid GST.
    invoice_date/bill_date are ISO-date STRINGS, matched with string month bounds.
    Fail-soft -> (0.0, 0.0, 0.0)."""
    if db is None:
        return 0.0, 0.0, 0.0
    try:
        entity_id = None
        store_gstin = ""
        try:
            _srow = db["stores"].find_one(
                {"store_id": active_store}, {"entity_id": 1, "gstin": 1}
            )
            entity_id = (_srow or {}).get("entity_id")
            store_gstin = str((_srow or {}).get("gstin", "") or "").strip()
        except Exception:
            entity_id = None
        month_lo = f"{year:04d}-{mon:02d}-01"
        month_hi = f"{year:04d}-{mon:02d}-{last_day:02d}T23:59:59"

        # NEW-GST-TRANSFER-OUTWARD: a transfer mirror bill's ITC belongs to the
        # RECEIVING GSTIN -- the same scope the return itself is filed under.
        # Keep a transfer bill only when its recipient_gstin matches the GSTIN
        # being filed (so every store of a multi-store GSTIN produces the SAME
        # Table 4 for that GSTIN -- one GSTIN, one filing); when the bill has no
        # recipient GSTIN on file, fall back to to_store_id == this store. The
        # outer $nor excludes transfer bills matching NEITHER keep-condition;
        # regular purchase bills (no source_transfer_id) are untouched. Without
        # this, on a same-entity cross-state transfer the SENDING store's 3B
        # would claim ITC on its own outward supply (netting its 3.1(a)
        # liability to zero). source_transfer_id uses {$exists,$ne None} to stay
        # aligned with the sender-side collector (_transfer_outward_bills) --
        # a None-valued field must not produce a one-sided claim.
        transfer_keep: list = []
        if store_gstin:
            transfer_keep.append({"recipient_gstin": store_gstin})
        transfer_keep.append(
            {"recipient_gstin": {"$in": ["", None]}, "to_store_id": active_store}
        )
        vb_match: dict = {
            "status": {"$nin": ["CANCELLED", "cancelled", "VOID", "voided"]},
            "itc_eligible": {"$ne": False},
            "$or": [
                {"invoice_date": {"$gte": month_lo, "$lte": month_hi}},
                {"bill_date": {"$gte": month_lo, "$lte": month_hi}},
            ],
            "$nor": [
                {
                    "source_transfer_id": {"$exists": True, "$ne": None},
                    "$nor": transfer_keep,
                }
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
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _itc_transfer_from_vendor_bills(db, active_store, year, mon, last_day):
    """R1: the TRANSFER-BORNE slice of Table-4 ITC -- ITC from inter-GSTIN
    stock-transfer mirror bills only (source_transfer_id set), kept for the
    GSTIN being filed. Returns (igst, cgst, sgst); fail-soft -> zeros.

    Unlike regular purchase ITC (entity-scoped -> identical across every sibling
    store of an entity), this slice is GSTIN-scoped: on a same-entity
    cross-state transfer only the RECEIVING GSTIN claims the credit, so two
    sibling stores of one entity with DIFFERENT GSTINs return DIFFERENT transfer
    ITC. gst_crosscheck.aggregate_gstr3b therefore dedupes this component once
    per GSTIN (and the regular remainder once per entity), making the entity
    figure independent of store enumeration order.

    Uses the SAME status / itc_eligible / date-window / entity / recipient-GSTIN
    keep filters as _itc_from_vendor_bills, restricted to transfer bills, so
    (this) + (regular-only bills) == _itc_from_vendor_bills total by
    construction -- reports.py stays the single source of tax truth."""
    if db is None:
        return 0.0, 0.0, 0.0
    try:
        entity_id = None
        store_gstin = ""
        try:
            _srow = db["stores"].find_one(
                {"store_id": active_store}, {"entity_id": 1, "gstin": 1}
            )
            entity_id = (_srow or {}).get("entity_id")
            store_gstin = str((_srow or {}).get("gstin", "") or "").strip()
        except Exception:
            entity_id = None
        month_lo = f"{year:04d}-{mon:02d}-01"
        month_hi = f"{year:04d}-{mon:02d}-{last_day:02d}T23:59:59"
        # Same keep-conditions as _itc_from_vendor_bills: a transfer mirror
        # bill's ITC belongs to the RECEIVING GSTIN (recipient_gstin == the
        # GSTIN being filed), falling back to to_store_id when the bill carries
        # no recipient GSTIN.
        transfer_keep: list = [
            {"recipient_gstin": {"$in": ["", None]}, "to_store_id": active_store},
        ]
        if store_gstin:
            transfer_keep.insert(0, {"recipient_gstin": store_gstin})
        vb_match: dict = {
            "status": {"$nin": ["CANCELLED", "cancelled", "VOID", "voided"]},
            "itc_eligible": {"$ne": False},
            "source_transfer_id": {"$exists": True, "$ne": None},
            "$and": [
                {
                    "$or": [
                        {"invoice_date": {"$gte": month_lo, "$lte": month_hi}},
                        {"bill_date": {"$gte": month_lo, "$lte": month_hi}},
                    ]
                },
                {"$or": transfer_keep},
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
    except Exception:
        pass
    return 0.0, 0.0, 0.0


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


