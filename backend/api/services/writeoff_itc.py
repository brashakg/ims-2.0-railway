"""Section 17(5)(h): input credit reversed on written-off stock.

Owner 2026-10-24 ("check yourself"), CA 2026-10-23: goods lost, stolen,
destroyed or written off take back the GST input credit claimed on their
purchase. The reversal is reported in GSTR-3B Table 4(B)(1) of the month of
the write-off, on the GSTIN that claimed the credit.

ONE rule for every write-off door:
  - record_writeoff(db, units, ...): one reversal row per written-off unit in
    `itc_writeoff_reversals`, keyed by the unit (`_id` = stock_id): writing a
    unit off twice, or a retry after a crash, never reverses twice. Returns
    the totals to show on the write-off record.
  - the bill: the one that brought the piece onto the GSTIN it is on now --
    the mirror bill of the newest inter-GSTIN crossing by ship time (every transfer the piece
    rode, so a later move inside one GSTIN keeps it; a crossing is a mirror,
    ship's stamp, else two shop GSTINs), its goods receipt's bills only when
    it rode no crossing; none while the newest crossing has no mirror booked
    yet (booked at Complete, after Receive; reversed when it is).
  - claimed, and on which GSTIN: gst_itc.bill_return_gstin -- the bill is in
    Table 4 (_itc_match) of that GSTIN's return. Credit no return places yet
    is stored `unallocated` (no GSTIN), never shown as reversed, and listed on
    GSTR-3B until the bill's GSTIN is settled.
  - how much: a bill line's heads H shared over its q pieces cumulatively --
    the k-th piece gets round(H*k/q) less what the receipt's rows already
    reversed (H and q over every bill of the receipt) -- so a whole line
    written off reverses exactly H, however the bills came in between. A header-only bill (or a transfer's aggregate
    line) shares its heads the same way, by the fraction of the receipt each
    piece is (its cost / the sum of the receipt's unit costs; when any unit
    is unpriced, 1 / the pieces the bill brought in), summed over the bill's
    rows: the whole bill written off reverses exactly its heads.
  - when: the write-off's IST month; a locked (filed) month moves it to the
    first open month, the original kept as `writeoff_period`.
  - month_reversal(db, gstin, period): what GSTR-3B Table 4(B)(1) adds;
    month_reclaim: re-claims, back in 4(A)(5), never netted into 4(B)(1).
  - reverse_on_new_bill(db, bill): a bill booked AFTER pieces of its receipt
    were written off reverses them, in the later of the loss month and the
    bill's month; a later part bill of a receipt tops its lost pieces' rows
    up to their slice of every bill (_true_up). A piece lost after a
    GSTIN-crossing transfer delivered it (on any later move too), before the
    mirror bill is booked, reverses when the mirror lands.
  - undo_writeoff(db, unit): a written-off piece back in play (re-quarantined,
    restocked) drops its reversal -- deleted while its month is open, else a
    re-claim row in the first open month.

ponytail: k is read from the rows already recorded, so two write-offs of the
same line at the same instant could share one k (a paisa off); an atomic
counter would need undo / crash bookkeeping of its own.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from ..utils.ist import ist_date_str

logger = logging.getLogger(__name__)

COLLECTION = "itc_writeoff_reversals"
_HEADS = ("igst", "cgst", "sgst")
COUNT_VOID_PREFIX = "cycle-count-reconcile:"


def _gst_itc():
    # Lazy: the reports package pulls in every report router.
    from ..routers.reports import gst_itc

    return gst_itc


def bill_return_gstin(db, bill):
    return _gst_itc().bill_return_gstin(db, bill)


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _sid(unit: dict) -> str:
    return str(unit.get("stock_id") or unit.get("_id"))


def _receipt_ids(unit: dict) -> list:
    ids = {unit.get("grn_id")}
    if unit.get("source_type") == "GRN":
        ids.add(unit.get("source_id"))
    ids.discard(None)
    return sorted(ids)


def _transfer_ids(db, unit: dict) -> list:
    """Every transfer this piece rode: the one holding it, the one that
    delivered it (_rehome clears transfer_id and stamps source_id), and every
    earlier one whose lines list it."""
    ids = {unit.get("transfer_id")}
    if unit.get("source_type") == "TRANSFER":
        ids.add(unit.get("source_id"))
    keys = list({str(unit.get("stock_id") or ""), str(unit.get("_id") or "")} - {""})
    for t in db.get_collection("stock_transfers").find(
        {"$or": [{"items.received_stock_ids": {"$in": keys}},
                 {"items.damaged_stock_ids": {"$in": keys}}]},
        {"id": 1},
    ):
        ids.add(t.get("id"))
    ids.discard(None)
    return sorted(ids)


def _receipt_bills(db, rids: list) -> list:
    return list(db.get_collection("vendor_bills").find({
        "status": {"$nin": _gst_itc()._DEAD_BILL},
        "$or": [{"grn_id": {"$in": rids}}, {"linked_dc_ids": {"$in": rids}}]}))


def _bills_for(db, unit: dict, gstin_of) -> list:
    """The live bills that brought this unit onto its current GSTIN."""
    rids = _receipt_ids(unit)
    tids = _transfer_ids(db, unit)
    if not tids:
        return _receipt_bills(db, rids) if rids else []
    # It rode a transfer: a bill counts only if it brought the piece onto the
    # GSTIN it is on now -- the newest crossing's mirror; the receipt's bills
    # only when it rode no crossing at all. A mirror is booked at Complete, a
    # step after Receive, so a crossing whose mirror is not booked yet holds
    # the reversal until it is (reverse_on_new_bill), never a stale bill.
    gstins = _gst_itc().shop_gstins(db)
    here = gstins.get(unit.get("store_id")) or ""

    def landed_here(b):
        # The GSTIN its return places it on, or its receiving shop's: credit
        # no return places yet is still that shop's, held unallocated.
        return here in {gstin_of(b) or None, gstins.get(b.get("to_store_id") or b.get("store_id"))}

    mirrors = {b.get("source_transfer_id"): b for b in db.get_collection("vendor_bills").find({
        "status": {"$nin": _gst_itc()._DEAD_BILL}, "source_transfer_id": {"$in": tids}})}
    moved = {t.get("id"): t for t in db.get_collection("stock_transfers").find(
        {"id": {"$in": tids}}, {"id": 1, "shipped_at": 1, "received_at": 1, "gst_registrations": 1,
                                "from_location_id": 1, "to_location_id": 1})}
    crossings = []
    for tid in tids:
        t, m = moved.get(tid) or {}, mirrors.get(tid) or {}
        stamp = t.get("gst_registrations")
        # Ship's stamp (None -- cannot tell -- books a mirror too), else the
        # two shops' GSTINs: the rule of transfers._transfer_registrations.
        crosses = bool(m) or (stamp.get("crosses") is not False if isinstance(stamp, dict) else
                              (gstins.get(t.get("from_location_id")) or "")
                              != (gstins.get(t.get("to_location_id")) or ""))
        if crosses:
            # Newest by when the transfer SHIPPED (stamped once; the piece can
            # only ship on B after it arrived on A). Never by the mirror's date
            # first (booked at COMPLETE, maybe after a later move) nor by
            # received_at (restamped by every receive call).
            crossings.append(((str(t.get("shipped_at") or ""), str(t.get("received_at") or ""),
                               str(m.get("bill_date") or ""), str(m.get("created_at") or "")), tid))
    if not crossings:
        return [b for b in _receipt_bills(db, rids) if landed_here(b)] if rids else []
    latest = mirrors.get(max(crossings)[1])
    return [latest] if latest and landed_here(latest) else []


def _transfer_units(db, tid: str) -> dict:
    """The units a transfer carried: by their stamps, and -- a piece that
    moved on since (_rehome restamps source_id) -- as listed on its lines."""
    listed = [s for t in db.get_collection("stock_transfers").find({"id": tid}, {"items": 1})
              for it in t.get("items") or []
              for s in (it.get("received_stock_ids") or []) + (it.get("damaged_stock_ids") or [])]
    return {"$or": [{"source_type": "TRANSFER", "source_id": tid}, {"transfer_id": tid},
                    {"stock_id": {"$in": listed}}]}


def _bill_unit_costs(db, bill: dict) -> list:
    """(cost, pieces) of every unit the bill's receipt (or transfer) brought
    in; cost 0 when the unit carries no price. Rejected custody is left out,
    as purchase_invoices does: the bill covers the accepted pieces.

    ponytail: a bill that also covers rejected pieces (billed rejects behind
    the rejected-goods hold) still shares its whole GST over the accepted pieces only; net
    the confirmed supplier credit off that bill's base if that shape appears."""
    if bill.get("source_transfer_id"):
        q = _transfer_units(db, bill["source_transfer_id"])
    else:
        rids = [r for r in [bill.get("grn_id"), *(bill.get("linked_dc_ids") or [])] if r]
        if not rids:
            return []
        q = {"$or": [{"grn_id": {"$in": rids}}, {"source_type": "GRN", "source_id": {"$in": rids}}],
             "receipt_disposition": {"$ne": "REJECTED"}}
    return [(_f(u.get("unit_cost")) or _f(u.get("cost_price")), _pieces(u))
            for u in db.get_collection("stock_units").find(
                q, {"unit_cost": 1, "cost_price": 1, "quantity": 1})]


def _pieces(unit: dict) -> float:
    return 1.0 if unit.get("quantity") is None else _f(unit.get("quantity"))


def _recorded(db, query: dict, count: str) -> tuple:
    """(sum of `count`, heads) over the live reversal rows matching `query`,
    net of what was re-claimed off a filed top-up row (it stays live)."""
    done, heads = 0.0, dict.fromkeys(_HEADS, 0.0)
    for r in db.get_collection(COLLECTION).find({**query, "$or": [
            {"kind": "REVERSAL", "superseded": {"$ne": True}}, {"kind": "RECLAIM", "topup": True}]}):
        done += _f(r.get(count))
        for h in _HEADS:
            heads[h] += _f(r.get(h))
    return done, heads


def _cumulative(heads: dict, recorded: dict, hi: float, whole: float = 1.0) -> dict:
    """The slice of the heads up to hi/whole, less what is already recorded:
    however bills or rows came in between, the slices add up to the heads
    exactly."""
    return {h: max(0.0, round(round(heads[h] * hi / whole, 2) - recorded[h], 2)) for h in _HEADS}


def _line_heads(bills: list, pid, gstin_of) -> tuple:
    """(ids of the bills with lines for pid, their claimed heads, their
    quantity, (first claiming bill, its GSTIN) or None)."""
    heads = dict.fromkeys(_HEADS, 0.0)
    qty, claimed, line_bills = 0.0, None, []
    for bill in bills:
        lines = [ln for ln in bill.get("lines") or [] if ln.get("product_id") == pid]
        if not lines:
            continue
        line_bills.append(bill.get("bill_id"))
        qty += sum(_f(ln.get("qty")) for ln in lines)
        g = gstin_of(bill)
        if g is None:
            continue
        for ln in lines:
            for h in _HEADS:
                heads[h] += _f(ln.get(h))
        claimed = claimed or (bill, g)
    return line_bills, heads, qty, claimed


def _unit_credit(db, unit: dict, gstin_of) -> Optional[dict]:
    """The credit claimed on this unit's pieces: {bill_id, gstin ('' when no
    return places it), entity_id, igst, cgst, sgst}, or None when none was."""
    pid = unit.get("product_id")
    n = _pieces(unit)
    bills = _bills_for(db, unit, gstin_of)
    line_bills, heads, qty, claimed = _line_heads(bills, pid, gstin_of)
    if line_bills:
        if claimed is None or qty <= 0:
            return None
        # Every bill on the receipt, whichever the find returns first.
        done, recorded = _recorded(db, {"bill_id": {"$in": line_bills}, "product_id": pid}, "pieces")
        amount = _cumulative(heads, recorded, min(done + n, qty), qty)
    else:
        # A header-only bill, or one whose lines name no product (a transfer's
        # aggregate line): its GST shared cumulatively by the piece's share
        # of the receipt's cost, else equally over the pieces it brought in.
        amount = None
        for bill in bills:
            lines = bill.get("lines") or []
            if lines and all(ln.get("product_id") for ln in lines):
                continue  # this bill's lines are other products'
            taxable = _f(bill.get("taxable_amount"))
            g = gstin_of(bill)
            if taxable <= 0 or g is None:
                continue
            # The piece's fraction of the receipt's OWN cost (a freight line
            # or a bill discount leaves that apart from the bill's taxable
            # value); any unpriced piece: an equal share each, never a mix.
            price = _f(unit.get("unit_cost")) or _f(unit.get("cost_price"))
            costs = _bill_unit_costs(db, bill)
            base = sum(c * q for c, q in costs) if costs and all(c for c, _q in costs) else 0.0
            share = (price / base if price and base else
                     1.0 / max(sum(q for _c, q in costs), 1.0)) * n
            done, recorded = _recorded(db, {"bill_id": bill.get("bill_id"),
                                            "share": {"$exists": True}}, "share")
            amount = _cumulative({h: _f(bill.get(f"{h}_total")) for h in _HEADS},
                                 recorded, min(done + share, 1.0))
            amount["share"] = share
            claimed = (bill, g)
            break
        if amount is None:
            return None
    # A nil slice (the bills so far fully reversed) is still recorded: the
    # piece counts as done, so the pieces after it -- or a later part bill's
    # top-up (_true_up) -- take the rest.
    bill, g = claimed
    return {"bill_id": bill.get("bill_id"), "gstin": g,
            "entity_id": bill.get("recipient_entity_id") or bill.get("entity_id"), **amount}


def _period(when) -> str:
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    return ist_date_str(when)[:7]


def _locked(db, period: str) -> bool:
    from ..routers.finance._shared import is_period_locked

    return is_period_locked(db, int(period[5:7]), int(period[:4]))


def _next_month(period: str) -> str:
    y, m = int(period[:4]), int(period[5:7])
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def _now() -> datetime:
    """Today, naive UTC (tests fake this one clock)."""
    return datetime.utcnow()


def _open_period(db, when) -> str:
    """The write-off's IST month, or -- when that return is locked (filed) --
    the first open month from today's."""
    period = _period(when)
    if not _locked(db, period):
        return period
    period = max(period, _period(_now()))
    for _ in range(24):
        if not _locked(db, period):
            break
        period = _next_month(period)
    return period


def totals(rows: Iterable[dict]) -> dict:
    """Sum reversal rows (or earlier totals). A row with no GSTIN is never
    shown as reversed: it counts under `unallocated_total` instead."""
    out = dict.fromkeys(_HEADS, 0.0)
    held = 0.0
    for r in rows:
        if r.get("unallocated") is True:
            held += sum(_f(r.get(h)) for h in _HEADS)
            continue
        for h in _HEADS:
            out[h] = round(out[h] + _f(r.get(h)), 2)
        held += _f(r.get("unallocated_total"))
    out["total"] = round(sum(out[h] for h in _HEADS), 2)
    if round(held, 2):
        out["unallocated_total"] = round(held, 2)
    return out


def _gstin_lookup(db):
    """bill_return_gstin, read once per bill."""
    cache: dict = {}

    def gstin_of(bill):
        key = bill.get("bill_id") or str(bill.get("_id"))
        if key not in cache:
            cache[key] = bill_return_gstin(db, bill)
        return cache[key]

    return gstin_of


def record_writeoff(db, units: Iterable[dict], *, source_type: str, source_id: str,
                    when, actor_id: str = "") -> dict:
    """Reverse the credit claimed on these written-off units (once per unit,
    ever) and return the totals of their reversal rows -- the first record's
    figures when a unit was already reversed. A unit that claimed no credit
    reverses nothing. A failed write raises: a write-off must not lose its
    GST reversal quietly; re-running this is safe."""
    coll = db.get_collection(COLLECTION)
    gstin_of = _gstin_lookup(db)
    ids = []
    for unit in units:
        sid = _sid(unit)
        if coll.find_one({"_id": sid}, {"_id": 1}):
            ids.append(sid)
            continue
        credit = _unit_credit(db, unit, gstin_of)
        if credit is None:
            continue
        period, booked = _open_period(db, when), _period(when)
        if not credit["gstin"]:
            logger.warning("[ITC] write-off of %s: bill %s carries credit no GSTIN's return "
                           "places -- reversal held as unallocated", sid, credit["bill_id"])
        coll.update_one(
            {"_id": sid},
            {"$setOnInsert": {
                "kind": "REVERSAL",
                "stock_id": sid, "product_id": unit.get("product_id"),
                "store_id": unit.get("store_id"), "pieces": _pieces(unit),
                "bill_id": credit["bill_id"], "gstin": credit["gstin"] or None,
                "unallocated": not credit["gstin"],
                "entity_id": credit["entity_id"], "period": period,
                **({"writeoff_period": booked} if booked != period else {}),
                **{h: credit[h] for h in _HEADS},
                **({"share": credit["share"]} if "share" in credit else {}),
                "source_type": source_type, "source_id": source_id,
                "recorded_by": actor_id, "recorded_at": datetime.utcnow(),
            }},
            upsert=True,
        )
        ids.append(sid)
    return totals(coll.find({"_id": {"$in": ids}}) if ids else [])


def _true_up(db, bills: list, pid, gstin_of, bill: Optional[dict] = None, when=None,
             actor_id: str = "") -> Optional[dict]:
    """Hold the receipt's rows for `pid` to exactly the lost pieces' slice of
    every bill's heads. A part bill booked after pieces were lost (`bill`):
    their rows were cut to the bills before it (a piece past the billed
    quantity got a nil row), so the rest goes on a top-up row of this bill in
    the open month of `when`. A piece back (no `bill`): top-ups give back
    what the rows now hold over that slice -- an open-month one shrinks; a
    filed one stands and the rest is re-claimed in the first open month."""
    line_bills, heads, qty, claimed = _line_heads(bills, pid, gstin_of)
    if claimed is None or qty <= 0:
        return None
    coll = db.get_collection(COLLECTION)
    lost, have = _recorded(db, {"bill_id": {"$in": line_bills}, "product_id": pid}, "pieces")
    target = {h: round(heads[h] * min(lost, qty) / qty, 2) for h in _HEADS}
    if bill is None:
        over = {h: round(have[h] - target[h], 2) for h in _HEADS}
        for row in coll.find({"topup": True, "kind": "REVERSAL", "product_id": pid,
                              "bill_id": {"$in": line_bills}}).sort("period", -1):
            locked = _locked(db, row["period"])
            net = {h: _f(row.get(h)) for h in _HEADS}
            for r in coll.find({"kind": "RECLAIM", "reclaims": row["_id"]}) if locked else []:
                net = {h: net[h] + _f(r.get(h)) for h in _HEADS}
            cut = {h: round(min(net[h], max(0.0, over[h])), 2) for h in _HEADS}
            if not any(cut.values()):
                continue
            if locked:
                # The filed return stands, as undo_writeoff keeps a filed row.
                period = _open_period(db, _now())
                body = {k: v for k, v in row.items() if k not in ("_id", *_HEADS)}
                coll.update_one({"_id": f"{row['_id']}:reclaim:{period}"}, {
                    "$inc": {h: -cut[h] for h in _HEADS},
                    "$setOnInsert": {**body, "kind": "RECLAIM", "reclaims": row["_id"],
                                     "period": period, "recorded_at": _now()}}, upsert=True)
            else:
                coll.update_one({"_id": row["_id"]}, {"$set": {h: round(net[h] - cut[h], 2)
                                                               for h in _HEADS}})
            over = {h: over[h] - cut[h] for h in _HEADS}
        return None
    g = gstin_of(bill)
    add = {h: max(0.0, round(target[h] - have[h], 2)) for h in _HEADS}
    if g is None or bill.get("bill_id") not in line_bills or not any(add.values()):
        return None
    period = _open_period(db, when)
    rid = f"{bill['bill_id']}:{pid}:topup:{period}"
    coll.update_one({"_id": rid}, {"$inc": add, "$setOnInsert": {
        "kind": "REVERSAL", "topup": True, "stock_id": None, "product_id": pid,
        "store_id": bill.get("to_store_id") or bill.get("store_id"), "pieces": 0.0,
        "bill_id": bill["bill_id"], "gstin": g or None, "unallocated": not g,
        "entity_id": bill.get("recipient_entity_id") or bill.get("entity_id"), "period": period,
        "source_type": "PART_BILL", "source_id": bill["bill_id"],
        "recorded_by": actor_id, "recorded_at": datetime.utcnow(),
    }}, upsert=True)
    return {**add, "unallocated": not g}


def _settle_unallocated(db) -> None:
    """A held (no-GSTIN) reversal -- of any month -- whose bill now lands on
    a return takes that GSTIN: the accountant settled the bill. Its month
    already filed (locked), it files in the first open month instead, the
    original kept as `writeoff_period` (record_writeoff's rule)."""
    coll = db.get_collection(COLLECTION)
    for row in coll.find({"unallocated": True}):
        bill = db.get_collection("vendor_bills").find_one({"bill_id": row.get("bill_id")})
        g = bill_return_gstin(db, bill) if bill else None
        if not g:
            continue
        fix = {"gstin": g, "unallocated": False}
        if row.get("period") and _locked(db, row["period"]):
            fix.update(period=_open_period(db, _now()),
                       writeoff_period=row.get("writeoff_period") or row["period"])
        coll.update_one({"_id": row["_id"], "unallocated": True}, {"$set": fix})


def month_reversal(db, gstin: str, period: str) -> dict:
    """GSTR-3B Table 4(B)(1), section 17(5)(h): the credit reversed on stock
    this GSTIN wrote off in `period` (YYYY-MM)."""
    if not gstin:
        return totals([])
    _settle_unallocated(db)
    return totals(db.get_collection(COLLECTION).find(
        {"gstin": gstin, "period": period, "kind": "REVERSAL"}))


def month_reclaim(db, gstin: str, period: str) -> dict:
    """Credit taken back in `period` on written-off pieces that came back
    after their month was filed: positive, it files in Table 4(A)(5), never
    netted into 4(B)(1).

    ponytail: CBIC Circular 170/02/2022 also lists a re-claim in 4(D)(1) for
    information; the portal file leaves 4(D)(1) nil until the CA confirms."""
    if not gstin:
        return totals([])
    rows = db.get_collection(COLLECTION).find({"gstin": gstin, "period": period, "kind": "RECLAIM"})
    return totals({h: -_f(r.get(h)) for h in _HEADS} for r in rows)


def unallocated_writeoffs(db, entity_id, period: str) -> dict:
    """Write-off reversals of `period` or earlier still held without a GSTIN
    (their bill is on no return yet), for GSTR-3B's allocation warning -- a
    filed month's held row warns on every later return until it is settled."""
    out = {"count": 0, "tax": 0.0, "bill_numbers": []}
    if not entity_id:
        return out
    try:
        _settle_unallocated(db)
        for row in db.get_collection(COLLECTION).find(
                {"unallocated": True, "period": {"$lte": period}, "entity_id": entity_id}):
            out["count"] += 1
            out["tax"] = round(out["tax"] + sum(_f(row.get(h)) for h in _HEADS), 2)
            if row.get("bill_id") not in out["bill_numbers"]:
                out["bill_numbers"].append(row.get("bill_id"))
    except Exception:
        return {**out, "failed": True}
    return out


def writeoff_source(unit: dict) -> tuple:
    """(source_type, source_id) of a written-off unit: the count that voided
    it, else its damage write-off."""
    reason = str(unit.get("void_reason") or "")
    if reason.startswith(COUNT_VOID_PREFIX):
        return "STOCK_COUNT", reason[len(COUNT_VOID_PREFIX):]
    return "QUARANTINE_DAMAGE", _sid(unit)


def _lost_at(db, unit: dict) -> Optional[datetime]:
    """When the piece was written off (naive UTC): the count's void stamp, else
    its DAMAGED event; None when neither is on record."""
    at = unit.get("voided_at")
    if not at:
        ev = db.get_collection("item_events").find_one(
            {"stock_id": unit.get("stock_id"), "to_state": "DAMAGED"}, sort=[("at", -1)])
        at = (ev or {}).get("at")
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(at, datetime):
        return None
    return at.astimezone(timezone.utc).replace(tzinfo=None) if at.tzinfo else at


def reverse_on_new_bill(db, bill: dict, actor_id: str = "") -> dict:
    """A bill booked AFTER pieces it covers were written off (voided by a
    count, or DAMAGED) claims credit on them: reverse it now, in the month of
    the loss -- or the bill's month when the loss came first (credit is never
    reversed before it is claimed). Once per piece, so safe to repeat. Every
    bill door calls this after storing the bill."""
    if bill.get("source_transfer_id"):
        whose = _transfer_units(db, bill["source_transfer_id"])
    else:
        rids = [r for r in [bill.get("grn_id"), *(bill.get("linked_dc_ids") or [])] if r]
        if not rids:
            return totals([])
        whose = {"$or": [{"grn_id": {"$in": rids}}, {"source_type": "GRN", "source_id": {"$in": rids}}]}
    gone = {"$or": [{"void_reason": {"$regex": "^" + COUNT_VOID_PREFIX}}, {"status": "DAMAGED"}]}
    day = str(bill.get("invoice_date") or bill.get("bill_date") or "")[:10]
    try:
        billed = datetime.fromisoformat(day) + timedelta(hours=6)  # 11:30 IST that day
    except ValueError:
        billed = datetime.utcnow()
    rows, latest = [], billed
    for unit in db.get_collection("stock_units").find({"$and": [whose, gone]}):
        source_type, source_id = writeoff_source(unit)
        lost = _lost_at(db, unit)
        when = max(billed, lost) if lost else billed
        latest = max(latest, when)
        rows.append(record_writeoff(db, [unit], source_type=source_type, source_id=source_id,
                                    when=when, actor_id=actor_id))
    if rows and not bill.get("source_transfer_id"):
        # One part bill of a receipt (a transfer has one mirror): the pieces
        # already reversed on the bills before it take its share now.
        gstin_of, receipt = _gstin_lookup(db), _receipt_bills(db, rids)
        for pid in {ln.get("product_id") for ln in bill.get("lines") or []} - {None}:
            rows.append(_true_up(db, receipt, pid, gstin_of, bill, latest, actor_id) or {})
    return totals(rows)


def after_bill_booked(db, bill: dict, actor_id: str = "") -> None:
    """reverse_on_new_bill for a bill door: the bill is already stored, so a
    failure here is logged loudly rather than failing the booking."""
    try:
        reverse_on_new_bill(db, bill, actor_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[ITC] write-off reversal for bill %s failed: %s", bill.get("bill_id"), exc)


def undo_writeoff(db, unit: dict) -> None:
    """The piece is back from its write-off: its reversal goes. While its
    month is open the row is deleted; once that return is locked (filed) it
    stays there and a re-claim row in the first open month adds the credit
    back. Each step is idempotent, so a cut-off run is safe to repeat."""
    coll = db.get_collection(COLLECTION)
    sid = _sid(unit)
    row = coll.find_one({"_id": sid})
    if not row:
        return
    if not _locked(db, row["period"]):
        coll.delete_one({"_id": sid})
        _trim_top_up(db, unit, row)
        return
    kept = f"{sid}:{row['period']}"
    body = {k: v for k, v in row.items() if k != "_id"}
    coll.update_one({"_id": kept}, {"$setOnInsert": {**body, "superseded": True}}, upsert=True)
    coll.update_one({"_id": f"{kept}:reclaim"}, {"$setOnInsert": {
        **body, "kind": "RECLAIM", "reclaims": kept,
        "period": _open_period(db, _now()),
        **{h: -_f(row.get(h)) for h in _HEADS},
        "recorded_at": _now(),
    }}, upsert=True)
    coll.delete_one({"_id": sid})
    _trim_top_up(db, unit, row)


def _trim_top_up(db, unit: dict, row: dict) -> None:
    if "share" not in row:  # a header-only bill's rows take no top-up
        gstin_of = _gstin_lookup(db)
        _true_up(db, _bills_for(db, unit, gstin_of), row.get("product_id"), gstin_of)


def forget(db, unit: dict) -> None:
    """Drop a reversal written ahead of a move that then lost its race."""
    db.get_collection(COLLECTION).delete_one({"_id": _sid(unit)})
