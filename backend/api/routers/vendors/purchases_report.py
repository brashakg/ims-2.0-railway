"""Purchases this month (audit F56, owner ruling 2026-09-28).

One row per vendor: what we ORDERED (orders sent in the month), RECEIVED
(goods PUT INTO STOCK in the month -- each unit in the month receiving minted
it, never a line held back for cataloguing, see _put_in_stock -- incl. GST at
the order's price, else the receipt line's own price -- see _received_line),
were BILLED, PAID, still OWE on the as-of day
(the month's end, today for the month we are in), and the NEXT DUE date on
that day. Billed / paid / owed are the supplier ledger's own rows
(ap_engine.build_ledger) over the one row rule every payable screen reads
(finance._ap_rows -> ap_engine.supplier_ledger_rows: no transfer mirror
bills; a shop's share when one shop is asked for), so this report can never
disagree with the vendor ledger, /finance/vendor-payments, AP aging or the
Cash Flow payables. Shop scope is the one Purchase rule (resolve_store_scope).

THE ONE 'WE OWE' RULE (F56, review round 3 #1). A vendor row's `owed` is
that supplier's own signed ledger balance on the as-of day: above 0 we owe
it, below 0 it holds money paid ahead of its bills (an advance). Its own
on-account money and advances settle its own bills first, never another
supplier's. So the totals never net one supplier against another:
  totals.owed     = SUM over suppliers of max(balance, 0)  -- 'we owe'
  totals.advances = SUM over suppliers of max(-balance, 0) -- paid ahead,
                    a figure apart, NEVER taken off totals.owed.
(_owed_and_ahead). Netting them let one supplier's advance cancel another
supplier's debt, and the report read Rs 5,000 'advance' while Rs 5,000 was
overdue to another supplier.

The body also says what the screen must say about its own figures:
  as_of            -- the day owed / next due are struck on (the month's end,
                      today for the month we are in);
  unpriced_receipt_lines -- accepted receipt lines with no price at all (no
                      order price and none on the receipt): Received counts
                      them 0;
  unassigned_owed / unassigned_advances -- all-stores view only: per
                      supplier, its balance on the rows the ledger's shop rule
                      puts in NO shop (a bill with no shop and the money
                      naming it, money recorded with no shop for a supplier
                      who has never billed), split by the same rule: what is
                      owed there, and what is paid ahead there. None in a
                      one-shop view. Per supplier, its shops' balances plus
                      its no-shop balance are its All-stores balance; the
                      owed totals add up the same way unless one supplier is
                      owed in one shop and paid ahead in another (All stores
                      settles the one against the other: it is one supplier).
"""

import re
from calendar import monthrange
from datetime import date

from ._shared import (
    Depends,
    HTTPException,
    Optional,
    Query,
    _AP_ROLES,
    _get_db,
    ap_engine,
    require_roles,
    resolve_store_scope,
    router,
)
from ...services import purchase_invoice_engine as pinv
from ...utils.ist import ist_date_str_from_stored, now_ist_naive

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_FIGURES = ("ordered", "received", "billed", "paid", "owed")
# Goods have been accepted into stock on a receipt in either state.
_ACCEPTED = ("ACCEPTED", "PARTIALLY_ACCEPTED")


def _month_of(value) -> str:
    """'YYYY-MM' of a stored date/instant, on the IST calendar ('' if none)."""
    return ist_date_str_from_stored(value)[:7]


def _find(db, coll: str, flt: dict, fields: Optional[dict] = None) -> list:
    return list(db.get_collection(coll).find(flt, {"_id": 0, **(fields or {})}))


def _money(value) -> float:
    """A stored price / rate as a 2dp float; 0.0 for none or junk."""
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _received_line(gi: dict, po: Optional[dict]) -> Optional[tuple]:
    """(qty, unit price, GST rate) one accepted receipt line counts at, or
    None for a line with nothing accepted.

    The ORDER line's price and rate first -- the very line the bill is drafted
    from (purchase_invoice_engine.lines_from_grn, asked about this one line).
    A legacy order line with no tax_rate counts WITHOUT GST, exactly as its
    bill draft does (lines_from_grn reads the missing rate as 0%; the
    accountant corrects it on the bill). A line the order does not price (a
    delivery challan or walk-in with no order, or a product not on the order)
    counts at the receipt line's OWN unit_price and rate -- the price
    receiving stamps on the stock it mints (grn_accept). Neither: price 0."""
    lines = pinv.lines_from_grn({"items": [gi]}, po)
    if not lines:
        return None
    line = lines[0]
    if line["unit_price"] > 0:
        return line["qty"], line["unit_price"], line["gst_rate"]
    rate = gi.get("tax_rate")
    if rate is None:
        rate = gi.get("gst_rate")
    rate = line["gst_rate"] if rate is None else _money(rate)
    return line["qty"], _money(gi.get("unit_price")), rate


def _accepted_qty(gi: dict) -> int:
    try:
        return max(int(gi.get("accepted_qty", 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def _put_in_stock(grn: dict, units: list, month: str) -> dict:
    """{receipt line index: units that line put INTO STOCK in `month`}.

    The truth is the stock itself. grn_accept mints one stock_units row per
    unit it puts on the shelf (source_type GRN, source_id = the receipt,
    grn_number, grn_line_index = its line), created_at = the moment it went
    in; `units` are those rows, found by what no later move rewrites
    (_minted_units), so a unit since moved to another shop still counts in
    the month it went into this one's stock. A line
    HELD at receiving (product not yet catalogued: unresolved_lines) mints
    nothing, so it counts nothing until a re-accept adds it -- in the month
    of that re-accept. The receipt's own accepted_at cannot say this: every
    accept, the re-accept included, overwrites it, which used to move a
    whole receipt (the lines received a month earlier too) into the
    re-accept's month and change a closed month's Received.

    A unit minted before units carried their line (no grn_line_index) goes
    on that receipt's lines of the same product, in line order, up to each
    line's accepted quantity. A line never counts more than it accepted.

    A receipt with NO unit on record (one accepted before units named their
    receipt) falls back on its own word: every line not held back, in the
    month it was accepted."""
    items = [gi if isinstance(gi, dict) else {} for gi in grn.get("items") or []]
    if not units:
        if _month_of(grn.get("accepted_at")) != month:
            return {}
        held = {h.get("product_id") for h in grn.get("unresolved_lines") or [] if isinstance(h, dict)}
        return {i: _accepted_qty(gi) for i, gi in enumerate(items) if gi.get("product_id") not in held}
    by_line: dict = {}
    unplaced: dict = {}
    for u in units:
        idx = u.get("grn_line_index")
        when = _month_of(u.get("created_at"))
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(items):
            by_line.setdefault(idx, []).append(when)
        else:
            unplaced.setdefault(u.get("product_id"), []).append(when)
    for pid, months in unplaced.items():
        months.sort()
        for i, gi in enumerate(items):
            room = _accepted_qty(gi) - len(by_line.get(i, ())) if gi.get("product_id") == pid else 0
            if room > 0 and months:
                by_line.setdefault(i, []).extend(months[:room])
                del months[:room]
    # Earliest first, so a line over its accepted quantity drops its latest.
    return {i: sorted(m)[: _accepted_qty(items[i])].count(month) for i, m in by_line.items()}


def _minted_units(db, grns: list) -> dict:
    """{grn_id: the stock_units its accepts minted} -- read from what the mint
    wrote and nothing later rewrites (review round 3 #3).

    A unit is the receipt's when it still names it (source_type GRN,
    source_id = grn_id) OR when it carries the receipt's grn_number. A
    transfer between our shops re-homes the unit (transfers._rehome) and
    OVERWRITES source_type / source_id with the transfer's, but never touches
    grn_number, grn_line_index, product_id or created_at -- all stamped by
    grn_accept's mint. grn_number is unique per receipt (the grns unique
    index; RCPT/<shop>/<FY>/<serial>). Matching on the source alone dropped a
    September receipt's units from September once they moved to another
    shop in October, so a closed month's Received changed with the goods'
    later moves.

    A serial captured against a receipt (serial_tracking) is a row of its
    own, not one the receipt's accept minted: never counted. Not narrowed to
    a shop: a unit moved to another shop was still received by this one.
    Narrowed to the receipts' products, which every unit they minted is (the
    mint stamps the line's product and nothing rewrites it), so the read
    walks the product index instead of every unit ever stocked."""
    ids = {g.get("grn_id") for g in grns if g.get("grn_id")}
    by_number = {g.get("grn_number"): g.get("grn_id") for g in grns if g.get("grn_number") and g.get("grn_id")}
    products = {
        gi.get("product_id")
        for g in grns
        for gi in g.get("items") or []
        if isinstance(gi, dict) and gi.get("product_id")
    }
    if not ids or not products:
        return {}
    named = [{"source_type": "GRN", "source_id": {"$in": list(ids)}}]
    if by_number:
        named.append({"grn_number": {"$in": list(by_number)}})
    units: dict = {}
    for u in _find(
        db,
        "stock_units",
        {"product_id": {"$in": list(products)}, "$or": named},
        {
            "source_type": 1,
            "source_id": 1,
            "grn_number": 1,
            "grn_line_index": 1,
            "product_id": 1,
            "created_at": 1,
            "serial_tracked": 1,
        },
    ):
        if u.get("serial_tracked"):
            continue
        gid = u.get("source_id") if u.get("source_type") == "GRN" and u.get("source_id") in ids else None
        gid = gid or by_number.get(u.get("grn_number"))
        if gid:
            units.setdefault(gid, []).append(u)
    return units


def _by_vendor(rows: tuple) -> dict:
    """{vendor_id: (bills, payments, notes)} -- the ledger rows, one supplier's
    at a time (a supplier's ledger is its own rows only)."""
    out: dict = {}
    for i, docs in enumerate(rows):
        for d in docs:
            out.setdefault(d.get("vendor_id"), ([], [], []))[i].append(d)
    return out


def _balance(rows: tuple) -> float:
    """One supplier's signed ledger balance on (bills, payments, notes):
    credit less debit over build_ledger's own entries (> 0 we owe it, < 0 it
    holds money paid ahead) -- the report's per-vendor `owed`."""
    return round(sum(e["credit"] - e["debit"] for e in ap_engine.build_ledger(*rows)["entries"]), 2)


def _owed_and_ahead(balances) -> tuple:
    """THE ONE 'WE OWE' RULE over per-supplier balances: (owed, paid ahead) =
    (SUM of max(balance, 0), SUM of max(-balance, 0)). Never netted: one
    supplier's advance does not pay another supplier's bill."""
    owed = ahead = 0.0
    for b in balances:
        if b > 0:
            owed += b
        else:
            ahead -= b
    return round(owed, 2), round(ahead, 2)


def _in_no_shop(db, as_of: str) -> Optional[tuple]:
    """(owed, paid ahead) on what THE ledger row rule
    (ap_engine.supplier_ledger_rows) places in no shop -- a bill with no shop
    and the money naming it, and money recorded with no shop for a supplier
    who has never billed -- over one snapshot of the rows. Per supplier: its
    All-stores balance less its balance in every shop any row is stamped with
    (so a shop a row is placed in is always counted), then split by
    _owed_and_ahead. None when the rows cannot be read."""
    try:
        raw = tuple(
            _find(db, coll, {}) for coll in ("vendor_bills", "vendor_payments", "vendor_debit_notes")
        )
    except Exception:
        return None
    shops = {d.get("store_id") for docs in raw for d in docs if isinstance(d, dict) and d.get("store_id")}
    left = {
        vid: _balance(rows)
        for vid, rows in _by_vendor(ap_engine.supplier_ledger_rows(*raw, None, as_of)).items()
    }
    for shop in shops:
        for vid, rows in _by_vendor(ap_engine.supplier_ledger_rows(*raw, shop, as_of)).items():
            left[vid] = left.get(vid, 0.0) - _balance(rows)
    return _owed_and_ahead(round(b, 2) for b in left.values())


@router.get("/purchases-this-month")
async def purchases_this_month(
    month: Optional[str] = Query(None, description="YYYY-MM (IST); default this month"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    month = month or now_ist_naive().strftime("%Y-%m")
    if not _MONTH.match(month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    scope = resolve_store_scope(store_id, current_user)
    month_end = date(int(month[:4]), int(month[5:]), monthrange(int(month[:4]), int(month[5:]))[1])
    # THE as-of day (ap_engine.as_of_day): the month's end, clamped to today
    # for the month we are in -- the day every other payable screen uses, so
    # a post-dated cheque is unpaid here exactly as it is there. Returned, so
    # the screen says 'owed as at <day>' instead of guessing the month's end.
    as_of = ap_engine.as_of_day(month_end.isoformat())
    body = {
        "month": month,
        "store_id": scope,
        "as_of": as_of,
        "vendors": [],
        # owed = what we owe (suppliers above 0 only); advances = what is paid
        # ahead (suppliers below 0), never taken off owed.
        "totals": {**dict.fromkeys(_FIGURES, 0.0), "advances": 0.0},
        "unpriced_receipt_lines": 0,
        "unassigned_owed": None,
        "unassigned_advances": None,
    }
    db = _get_db()
    if db is None:
        return body

    from ..finance import _ap_rows  # the one AP row loader (call time: no cycle)

    shop = {"store_id": scope} if scope else {}
    bills, payments, notes = _ap_rows(db, scope, as_of)

    rows: dict = {}

    def row(vendor_id):
        return rows.setdefault(vendor_id, dict.fromkeys(_FIGURES, 0.0))

    # ORDERED: orders sent to the vendor this month (drafts and cancelled never).
    for po in _find(
        db,
        "purchase_orders",
        {
            **({"delivery_store_id": scope} if scope else {}),
            "status": {"$nin": ["DRAFT", "CANCELLED"]},
        },
    ):
        if _month_of(po.get("sent_at") or po.get("created_at")) == month:
            row(po.get("vendor_id"))["ordered"] += float(po.get("total_amount") or po.get("total") or 0)

    # RECEIVED: the units each receipt put INTO STOCK this month
    # (_put_in_stock), incl. GST, at the price _received_line gives the line
    # (the order's, else the receipt line's own). A line with no price anywhere
    # counts 0 and is counted in unpriced_receipt_lines, so the screen can say
    # so. A receipt can only have put units in stock up to its LAST accept
    # (accepted_at, rewritten by every accept), so one last accepted before
    # this month is out -- unless it still waits on cataloguing
    # (PARTIALLY_ACCEPTED): a re-accept that stopped half-way can have added
    # units without restamping it.
    grns = [
        g
        for g in _find(db, "grns", {**shop, "status": {"$in": list(_ACCEPTED)}})
        if g.get("status") == "PARTIALLY_ACCEPTED" or _month_of(g.get("accepted_at")) >= month
    ]
    units = _minted_units(db, grns)
    po_ids = list({g.get("po_id") for g in grns if g.get("po_id")})
    pos_by_id = {p.get("po_id"): p for p in _find(db, "purchase_orders", {"po_id": {"$in": po_ids}})}
    for g in grns:
        po = pos_by_id.get(g.get("po_id"))
        items = g.get("items") or []
        for i, qty in _put_in_stock(g, units.get(g.get("grn_id")) or [], month).items():
            counted = _received_line(items[i], po) if qty > 0 else None
            if counted is None:
                continue
            _, price, rate = counted
            if price <= 0:
                body["unpriced_receipt_lines"] += 1
                continue
            row(g.get("vendor_id"))["received"] += qty * price * (1 + rate / 100)

    # BILLED / PAID / OWED: the supplier ledger, row for row, as it stood on the
    # as-of day (_ap_rows already dropped rows dated later; undated rows count,
    # as in the closing balance). A row's month is its ledger day's month.
    ledgers = _by_vendor((bills, payments, notes))
    next_due: dict = {}
    for vid in set(ledgers) | set(rows):
        v_bills, v_pays, v_notes = ledgers.get(vid, ([], [], []))
        r = row(vid)
        for entry in ap_engine.build_ledger(v_bills, v_pays, v_notes)["entries"]:
            r["owed"] += entry["credit"] - entry["debit"]
            if ap_engine.ledger_day(entry)[:7] == month:
                if entry["type"] == "BILL":
                    r["billed"] += entry["credit"]
                elif entry["type"] == "PAYMENT":
                    r["paid"] += entry["debit"]
        # The earliest due date of a bill still owed at the month's end, after
        # on-account money has settled the oldest (ap_engine.build_aging).
        dues = [
            str(it["due_date"])[:10]  # a stored datetime and a string both compare
            for it in ap_engine.build_aging(v_bills, v_pays, v_notes, as_of)["items"]
            if it.get("due_date")
        ]
        next_due[vid] = min(dues) if dues else None

    names = {
        v.get("vendor_id"): v.get("trade_name") or v.get("legal_name") or v.get("name")
        for v in _find(db, "vendors", {"vendor_id": {"$in": list(rows)}})
    }
    for vid, r in rows.items():
        figures = {k: round(r[k], 2) for k in _FIGURES}
        if not any(figures.values()):
            continue
        body["vendors"].append(
            {
                "vendor_id": vid,
                "vendor_name": names.get(vid) or vid,
                **figures,
                "next_due_date": next_due.get(vid),
                # Past due on the day the report is as at (month end / today).
                "next_due_overdue": bool(next_due.get(vid) and next_due[vid] < as_of),
            }
        )
        for k in _FIGURES:
            if k != "owed":
                body["totals"][k] = round(body["totals"][k] + figures[k], 2)
    # THE ONE 'WE OWE' RULE: owed adds only the suppliers we owe; what is paid
    # ahead to others is its own figure, never subtracted (_owed_and_ahead).
    body["totals"]["owed"], body["totals"]["advances"] = _owed_and_ahead(v["owed"] for v in body["vendors"])
    body["vendors"].sort(key=lambda v: -v["owed"])
    if scope is None:
        body["unassigned_owed"], body["unassigned_advances"] = _in_no_shop(db, as_of) or (None, None)
    return body
