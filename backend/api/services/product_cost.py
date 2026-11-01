"""IMS 2.0 - ONE average cost per product (S4; owner MCQ 2026-10-23, which
confirms 2026-09-28: "re-averaged only when the bill is ACCEPTED/released;
shelf units carry the same cost as the product").

Every piece of a product carries the product's one average cost
(stock_value.average_cost): GST-exclusive, with the GST a bill cannot claim
back inside it (CA Q12). The PO rate is only a provisional cost until a bill
is accepted (vendors/gst._promote_cost_from_rate fills it when there is none).
The average moves only here:

  * a vendor bill is ACCEPTED (booked with no hold, or its hold approved):
        new = (basis x old average + value of the good pieces billed)
              / (basis + good pieces billed)
    `basis` = the pieces we hold (shelf, reserved, on the way between shops,
    or off the shelf but still ours: quarantined, damaged, under a count)
    that a bill has already costed. Pieces of a receipt that still has
    unbilled pieces -- this bill's own receipt or delivery challans among
    them -- are left out: they carry the average only provisionally until
    their own bill, so a challan's pieces are never counted twice. Rejected
    pieces never count (no more pieces than the receipts accepted). A product
    with no average yet -- or only a PROVISIONAL cost (a PO or receipt rate,
    no history row) -- takes the bill's price: the first real bill REPLACES a
    provisional cost, never blends with it. A walk-in (no-PO) bill averages
    in like every other bill (owner 2026-11-01 (b); this replaced the
    2026-10-12 "latest price paid" rule).
  * PIECES SOLD BEFORE THEIR BILL (owner 2026-11-01 (a)): only the billed
    pieces we still HOLD are averaged in at the bill price; for those already
    sold, (bill price - the cost their sale line froze) x pieces is a
    COST-OF-GOODS VARIANCE, recorded once in the bill's history row and in
    `cogs_variances` (variance_by is THE reader the P&L / margin reports add
    to their COGS). A piece WRITTEN OFF before its bill is trued up the same
    way, at (bill price - the average its write-off loss was taken at). Each
    bill CLAIMS the gone pieces it took (history row `claimed`), even at no
    variance, so a second bill on the same receipt takes the next ones.
  * accounts correct it, with a reason (correct()) -- the one correction
    door: the product and catalogue editors only type a FIRST cost
    (refuse_editor_cost).
  * opening stock, or a first cost typed in an editor, gives a product that
    has none its first cost (seed(); seed_editor_cost()); a product created
    with a cost carries its first history row from the insert (born_with_cost).

Sales, transfers and counts never change it. Each change is one row of the
product's cost_history (the product page shows it; no per-piece cost).

EXACTLY ONCE, with no transactions (prod MongoDB is a single server): the
history row, its key (cost_keys) and the new average land in ONE
single-document update, guarded on the exact keys and the average it was
worked out from. A retry or a crash re-run never applies a bill twice; two
bills accepted at once never lose one (the loser re-reads and re-works), even
when the first left the average where it was. A bill that moves no average
still puts its key on every product it bills (mark), and is marked APPLIED
only once every such product holds it (applied); until then it stays
PENDING for the sweeper.
Money is exact to the paisa (Decimal, half up).
No emoji (Windows cp1252).
"""

import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from api.utils.event_time import utc_now_iso

from . import purchase_match as pmatch
from . import stock_value
from .item_events import StockState, canonical_state, is_on_hand

logger = logging.getLogger(__name__)

# A vendor bill's cost_state (S4). HELD: on hold, its cost waits for the
# approval. PENDING: accepted, its cost being applied (finish_accepted_bills
# finishes one a crash left half-done). APPLIED: done. A bill booked before S4
# has none: its cost went in when it was booked.
HELD, PENDING, APPLIED = "HELD", "PENDING", "APPLIED"
# A cost a purchase step fills before any bill (vendors/gst._promote_cost_from_rate):
# a later step may replace an earlier one (the rank), and the first real bill
# replaces any of them -- a provisional cost is no average.
PROVISIONAL_RANK = {"PO_RATE": 1, "GRN_PO": 2, "GRN_WALKIN": 2}
VARIANCES = "cogs_variances"
_BOOKED = {"OUTSTANDING", "PARTIAL", "PAID"}
_PAISA = Decimal("0.01")


def bill_accepted(bill: dict) -> bool:
    """THE 'is this vendor bill accepted' rule: booked, and not on hold."""
    return (
        str((bill or {}).get("status") or "").upper() in _BOOKED
        and (bill or {}).get("match_status") != pmatch.MATCH_ON_HOLD
    )


def _d(v) -> Decimal:
    return Decimal(str(pmatch._f(v)))


def _paise(v: Decimal) -> float:
    return float(v.quantize(_PAISA, rounding=ROUND_HALF_UP))


def provisional(product: Optional[dict]) -> bool:
    """A cost only a PO or receipt rate filled (no history row): replaced by
    the first real bill, opening stock or a first cost -- never averaged."""
    product = product or {}
    return (
        bool(stock_value.average_cost(product))
        and not product.get("cost_history")
        and str(product.get("cost_source") or "") in PROVISIONAL_RANK
    )


def average(product: Optional[dict]) -> float:
    """THE average a bill blends with (and a first cost may not replace):
    stock_value.average_cost, 0.0 for a provisional cost."""
    return 0.0 if provisional(product) else stock_value.average_cost(product)


# Still ours, not sold (owner 2026-11-01: "still held"): off the shelf too --
# quarantined, damaged, under audit or count -- it carries the average.
_STILL_OURS = {StockState.QUARANTINED, StockState.DAMAGED, StockState.UNDER_AUDIT, StockState.BLIND_COUNT,
               StockState.TRANSFERRED}


def _held(unit: dict) -> bool:
    """A piece we still hold: on the shelf, reserved, on the way, or off the
    shelf but still ours (_STILL_OURS)."""
    raw = unit.get("status")
    return is_on_hand(raw, include_reserved=True) or canonical_state(raw) in _STILL_OURS


def _written_off(unit: dict) -> bool:
    """A piece written off (a stock count's shrinkage: VOID)."""
    return canonical_state(unit.get("status")) is StockState.VOID


def _receipt_of(unit: dict) -> Optional[str]:
    """The goods receipt (or delivery challan) a piece came in on -- it
    survives a transfer."""
    if unit.get("grn_id"):
        return unit["grn_id"]
    return unit.get("source_id") if unit.get("source_type") == "GRN" else None


# A receipt accepted before S4 that no bill has costed under S4 (the backfill
# stamps it with its run id): its pieces carry the average they came in at, so
# they are part of the basis -- the first S4 bill blends with them instead of
# replacing the average. An S4 bill on it later prices it as usual.
PRE_S4_STAMP = "costed_before_s4"


def _links(bill: dict, receipt_id: str) -> bool:
    return bill.get("grn_id") == receipt_id or receipt_id in (bill.get("linked_dc_ids") or [])


def _costs_its_receipt(bill: dict, cost_keys) -> bool:
    if bill.get("cost_state") is None:  # booked before S4
        return bill_accepted(bill)
    return (bill.get("invoice_id") or bill.get("bill_id")) in cost_keys


def basis_pieces(db, product_id: str, cost_keys=()) -> float:
    """The pieces of a product we hold that a bill has already costed (see
    the module doc); `cost_keys`: the bills already in its average."""
    held: Dict[Optional[str], float] = {}
    for unit in db.get_collection("stock_units").find({"product_id": product_id}):
        if _held(unit):
            r = _receipt_of(unit)
            held[r] = held.get(r, 0.0) + stock_value._pieces(unit)
    total = sum(held.values())
    ids = [r for r in held if r]
    if not ids:
        return total
    receipts = {
        g["grn_id"]: g
        for g in db.get_collection("grns").find({"grn_id": {"$in": ids}}, {"_id": 0})
    }
    bills = list(db.get_collection("vendor_bills").find(
        {"$or": [{"grn_id": {"$in": ids}}, {"linked_dc_ids": {"$in": ids}}]}, {"_id": 0}
    ))
    for r in ids:
        if r not in receipts:  # a legacy piece whose receipt is gone: costed
            continue
        if receipts[r].get(PRE_S4_STAMP) and not any(
            b.get("cost_state") is not None and _links(b, r) for b in bills
        ):
            continue  # accepted before S4 with no S4 bill: it carries the old cost
        unbilled = float(pmatch._grn_accepted_by_product(receipts[r]).get(product_id, 0))
        for b in bills:
            if not _costs_its_receipt(b, cost_keys):
                continue
            if r in (b.get("linked_dc_ids") or []) or (b.get("grn_id") == r and not b.get("lines")):
                unbilled = 0.0  # a challan bill, or a header-only bill: the whole receipt
                break
            if b.get("grn_id") == r:
                unbilled -= sum(
                    pmatch._f(ln.get("qty")) for ln in b["lines"] if ln.get("product_id") == product_id
                )
        total -= min(held[r], max(0.0, unbilled))
    return total


def _move(
    db,
    product_id: str,
    key: str,
    work: Callable[[dict, float], Optional[Tuple[float, dict]]],
    entry: dict,
) -> Optional[dict]:
    """Apply one change to a product's average exactly once (module doc).
    `work(product, old_average)` -> (new average, extra history fields), or
    None to leave it. Returns the history row written, None when nothing was
    (already applied, nothing to do, or no such product)."""
    products = db.get_collection("products")
    for _ in range(5):
        prod = products.find_one({"product_id": product_id})
        if prod is None or key in (prod.get("cost_keys") or []):
            return None
        old = stock_value.average_cost(prod)
        worked = work(prod, old)
        if worked is None:
            return None
        new, extra = worked
        at = utc_now_iso()
        row = {**entry, **extra, "key": key, "at": at, "old_cost": old or None, "new_cost": new}
        done = products.update_one(
            {
                "product_id": product_id,
                # The keys the work was based on, exactly (not only "this key
                # absent"): a bill that landed meanwhile -- even one that left
                # the average where it was -- changes the basis, so re-work.
                "cost_keys": prod.get("cost_keys") or {"$in": [None, []]},
                "cost_price": prod.get("cost_price"),
                "cost_price_is_catalogue_only": prod.get("cost_price_is_catalogue_only"),
            },
            {
                "$set": {
                    "cost_price": new,
                    "moving_avg_cost": new,
                    "cost_price_is_catalogue_only": False,
                    "cost_valuation_pending": False,
                    "cost_updated_at": at,
                    "cost_source": entry.get("source"),
                    "cost_source_id": entry.get("bill_id") or key,
                },
                "$push": {"cost_history": row, "cost_keys": key},
            },
        )
        if done.modified_count:
            # The catalogue copy shows the same cost, by THE spine -> twin
            # mirror rule (fail-soft: the product is the record). A cost is in
            # no pushed payload: nothing queues.
            from .product_master import mirror_update_to_catalog_twin

            mirror_update_to_catalog_twin(
                product_id=product_id, current=prod, patch={"cost_price": new}, db=db, mark_dirty=False
            )
            return row
    logger.error("[COST] could not move the average of %s for %s: it kept changing", product_id, key)
    return None


def apply_bill(
    db,
    bill: dict,
    lines: List[dict],
    *,
    key: str,
    actor: Optional[str],
    receipts: Optional[List[dict]] = None,
) -> List[dict]:
    """Re-average every product an accepted bill's goods lines name. `lines`
    are its COST lines (GST it cannot claim already added:
    purchase_invoices._cost_lines). Only the billed pieces we still hold are
    averaged in; the sold ones give a cost-of-goods variance (module doc).
    Every product the bill names ends up holding its key, moved or not
    (mark), so its receipt's pieces join the basis. Returns the history rows
    written."""
    billed: Dict[str, List[Decimal]] = {}
    for ln in lines or []:
        pid, qty = ln.get("product_id"), _d(ln.get("qty"))
        if not pid or qty <= 0:
            continue
        line_value = _d(ln["taxable"]) if ln.get("taxable") is not None else qty * _d(ln.get("unit_price"))
        acc = billed.setdefault(pid, [Decimal(0), Decimal(0)])
        acc[0] += qty
        acc[1] += line_value
    good: Optional[Dict[str, float]] = None
    if receipts:
        good = {}
        for g in receipts:
            for pid, n in pmatch._grn_accepted_by_product(g).items():
                good[pid] = good.get(pid, 0) + n
    rows = []
    for pid, (qty, val) in billed.items():
        if good is not None:
            cap = Decimal(str(good.get(pid, 0)))
            if cap <= 0:  # every piece billed was rejected: nothing reached stock
                mark(db, pid, key)
                continue
            if qty > cap:  # rejected pieces never count
                val, qty = val * cap / qty, cap
        price = _paise(val / qty)

        def work(prod, old, pid=pid, qty=qty, val=val, price=price):
            keys = list(prod.get("cost_keys") or ())
            basis = Decimal(str(basis_pieces(db, pid, keys)))
            held = qty
            if receipts:
                # The billed pieces still held = what this bill adds to the
                # basis (a receipt's sold pieces are taken as billed first).
                after = Decimal(str(basis_pieces(db, pid, keys + [key])))
                held = min(qty, max(Decimal(0), after - basis))
            avg = Decimal(str(average(prod)))
            extra = {"qty": float(qty), "price": price, "held": float(held),
                     "basis": float(basis) if avg else None}
            if avg and basis + held > 0:
                new = _paise((basis * avg + val * held / qty) / (basis + held))
            else:  # no average yet, only a provisional one, or nothing held at all
                new = price
            if held < qty:
                claimed, var = _variance(db, prod, pid, receipts, int(qty - held), val / qty)
                if claimed:  # even at no variance: a later bill on the receipt takes the next ones
                    extra["claimed"] = claimed
                if var:
                    extra["variance"] = var
            return new, extra

        row = _move(db, pid, key, work, {
            "kind": "BILL",
            "source": "PURCHASE_INVOICE",
            "bill_id": bill.get("invoice_id") or bill.get("bill_id"),
            "bill_number": bill.get("invoice_number") or bill.get("bill_number"),
            "vendor_name": bill.get("vendor_name"),
            "method": "AVERAGE",
            "by": actor,
        })
        if row:
            rows.append({**row, "product_id": pid})
        else:  # applied before (a retry, a crash re-run): its history row says what
            prod = db.get_collection("products").find_one({"product_id": pid}) or {}
            row = next((r for r in prod.get("cost_history") or [] if r.get("key") == key), None)
        _record_variance(db, pid, bill, key, row)
    return rows


def _average_at(prod: dict, at) -> float:
    """The average in force at instant `at` (it moves only at a history row)."""
    rows = sorted(prod.get("cost_history") or [], key=lambda r: str(r.get("at") or ""))
    before = [r for r in rows if str(r.get("at") or "") <= str(at or "")]
    if before:
        return pmatch._f(before[-1].get("new_cost"))
    after = rows[0].get("old_cost") if rows else None
    return pmatch._f(after) or stock_value.average_cost(prod)


def _split(acc: Dict[str, Decimal], amount: float) -> List[dict]:
    """Per-key paisa amounts that add up to `amount` exactly (the rounding
    remainder goes to the largest)."""
    out = {k: v.quantize(_PAISA, rounding=ROUND_HALF_UP) for k, v in acc.items()}
    big = max(acc, key=lambda k: abs(acc[k]))
    out[big] += Decimal(str(amount)) - sum(out.values())
    return [{"key": k, "amount": float(v)} for k, v in out.items() if v]


def _variance(db, prod: dict, pid: str, receipts, gone: int, unit: Decimal) -> Tuple[List[str], Optional[dict]]:
    """The `gone` billed pieces no longer held -- the bill's receipts' pieces
    of `pid` that left on a sale or were written off, oldest first, none an
    earlier bill claimed -- as (every piece claimed, variance or None). Each
    is trued up at (bill unit price - the cost it left at): its sale line's
    cost_at_sale, or the average in force when it was written off. A sale
    line with no frozen cost is read at the product's live cost by the COGS
    readers: claimed, no variance. The variance carries its split per order
    line item_type and category (by_item_type / by_category: the keys the
    P&L readers group on)."""
    ids = [g["grn_id"] for g in receipts or [] if g.get("grn_id")]
    if not ids or gone <= 0:
        return [], None
    taken = {s for r in history(prod) for s in r.get("claimed") or []}
    units = sorted(
        (u for u in db.get_collection("stock_units").find({"product_id": pid})
         if _receipt_of(u) in ids and u.get("stock_id") not in taken
         and ((u.get("order_id") and not _held(u)) or _written_off(u))),
        key=lambda u: str(u.get("sold_at") or u.get("voided_at") or ""),
    )[:gone]
    orders = {
        o["order_id"]: o for o in db.get_collection("orders").find(
            {"order_id": {"$in": [u["order_id"] for u in units if u.get("order_id")]}},
            {"_id": 0, "order_id": 1, "items": 1})
    }
    stock_ids, at_sale, written_off = [], Decimal(0), 0
    by_type: Dict[str, Decimal] = {}
    by_cat: Dict[str, Decimal] = {}
    for u in units:
        if _written_off(u):
            # ponytail: no sale line -- the product's category for both keys.
            cost, kt = _average_at(prod, u.get("voided_at")), prod.get("category")
            kc = kt
            written_off += 1
        else:
            line = next((it for it in (orders.get(u["order_id"]) or {}).get("items") or []
                         if it.get("product_id") == pid and it.get("cost_at_sale") is not None), None)
            if line is None:
                continue
            cost, kt, kc = line["cost_at_sale"], line.get("item_type") or line.get("category"), line.get("category")
        stock_ids.append(u["stock_id"])
        at_sale += _d(cost)
        by_type[kt or "OTHER"] = by_type.get(kt or "OTHER", Decimal(0)) + unit - _d(cost)
        by_cat[kc or "Other"] = by_cat.get(kc or "Other", Decimal(0)) + unit - _d(cost)
    claimed = [u["stock_id"] for u in units]
    amount = _paise(unit * len(stock_ids) - at_sale)
    if not stock_ids or not amount:
        return claimed, None
    return claimed, {"pieces": len(stock_ids), "stock_ids": stock_ids, "receipt_ids": ids,
                     "bill_price": _paise(unit), "sold_at_cost": _paise(at_sale), "amount": amount,
                     "written_off": written_off,
                     "by_item_type": _split(by_type, amount), "by_category": _split(by_cat, amount)}


def _record_variance(db, pid: str, bill: dict, key: str, row: Optional[dict]) -> None:
    """The bill's row in `cogs_variances`, from its history row: one per
    (bill, product), never doubled (an upsert on its id; a crash re-run
    writes it from the history row). Dated by the bill's IST day."""
    var = (row or {}).get("variance")
    if not var:
        return
    from api.utils.ist import ist_date_str_from_stored

    day = ist_date_str_from_stored(bill.get("bill_date") or bill.get("invoice_date") or bill.get("created_at"))
    prod = db.get_collection("products").find_one({"product_id": pid}, {"_id": 0, "category": 1}) or {}
    vid = f"{key}:{pid}"
    try:
        db.get_collection(VARIANCES).update_one(
            {"variance_id": vid},
            {"$setOnInsert": {
                **var, "variance_id": vid, "bill_id": row.get("bill_id") or key,
                "bill_number": row.get("bill_number"), "vendor_name": row.get("vendor_name"),
                "product_id": pid, "category": prod.get("category"), "store_id": bill.get("store_id"),
                "day": day, "period": day[:7], "at": row.get("at"), "by": row.get("by"),
            }},
            upsert=True,
        )
    except DuplicateKeyError:  # a twin run inserted it first (unique variance_id)
        pass


def variance_by(db, field: Optional[str] = None, *, store_ids=None, from_day=None, to_day=None) -> Dict:
    """THE cost-of-goods variance reader (owner 2026-11-01 (a)): the bill
    true-up of pieces sold before their bill, which every P&L / margin report
    adds to its COGS. Summed per `field` (None: one total under the key None;
    "store_id"; "item_type" / "category": the sold lines' own keys, as
    /finance/pnl/by-category and growth.profit_by_category group them), bills
    dated (IST day) within the range, to the paisa."""
    q: dict = {}
    if store_ids is not None:
        q["store_id"] = {"$in": list(store_ids)}
    if from_day:
        q.setdefault("day", {})["$gte"] = str(from_day)[:10]
    if to_day:
        q.setdefault("day", {})["$lte"] = str(to_day)[:10]
    out: Dict = {}
    if db is None:
        return out
    for v in db.get_collection(VARIANCES).find(q):
        split = v.get(f"by_{field}") if field else None
        for k, amount in ([(s["key"], s["amount"]) for s in split] if split is not None
                          else [(v.get(field) if field else None, v.get("amount"))]):
            out[k] = out.get(k, Decimal(0)) + _d(amount)
    return {k: _paise(v) for k, v in out.items()}


def mark(db, product_id: str, key: str) -> None:
    """Record that bill `key` is in this product's average although it moved
    nothing (one guarded single-document update, no average change): without
    it the bill's receipt would wait for good, its pieces out of the basis."""
    db.get_collection("products").update_one(
        {"product_id": product_id, "cost_keys": {"$ne": key}}, {"$push": {"cost_keys": key}}
    )


def billed_products(lines) -> List[str]:
    """The products a bill's goods lines name (a positive quantity)."""
    return sorted({ln["product_id"] for ln in lines or [] if ln.get("product_id") and _d(ln.get("qty")) > 0})


def applied(db, lines, key: str) -> bool:
    """Whether bill `key` is in the average of every product its lines name
    (a product that no longer exists counts as done), its cost-of-goods
    variance rows written -- THE test before a bill is marked APPLIED, so a
    failed or half-done apply stays PENDING for the sweeper."""
    pids = billed_products(lines)
    if not pids:
        return True
    found = list(db.get_collection("products").find(
        {"product_id": {"$in": pids}}, {"product_id": 1, "cost_keys": 1, "cost_history": 1}))
    if not all(key in (p.get("cost_keys") or []) for p in found):
        return False
    owed = [f"{key}:{p['product_id']}" for p in found
            if any(r.get("key") == key and r.get("variance") for r in p.get("cost_history") or [])]
    return not owed or db.get_collection(VARIANCES).count_documents({"variance_id": {"$in": owed}}) == len(owed)


class CostChanged(Exception):
    """The average is no longer the one a correction was typed against."""


def correct(
    db, product_id: str, new_cost: float, reason: str, actor: Optional[str], *, expected
) -> Optional[dict]:
    """An accounts correction of a product's average, with its reason.
    `expected`: the average the person read (None: it had none). Raises
    CostChanged when the stored average is no longer that (a bill landed
    meanwhile) -- re-checked on every re-read of the guarded write. None when
    the average already is `new_cost`."""
    new = _paise(_d(new_cost))

    def work(_prod, old):
        if _paise(_d(old)) != _paise(_d(expected)):
            raise CostChanged(old)
        return None if new == old else (new, {})

    return _move(
        db, product_id, f"correction:{uuid.uuid4().hex}", work,
        {"kind": "CORRECTION", "source": "CORRECTION", "reason": reason, "by": actor},
    )


def seed(db, product_id: str, cost: float, *, key: str, kind: str, actor: Optional[str]) -> Optional[dict]:
    """A product's FIRST cost (opening stock; the backfill): written only
    while it has none -- a provisional cost is none (average())."""
    first = _paise(_d(cost))
    return _move(
        db, product_id, key,
        lambda prod, _old: None if average(prod) or first <= 0 else (first, {}),
        {"kind": kind, "source": kind, "by": actor},
    )


# Never in an editor door's plain write (seed_editor_cost writes a first cost).
NOT_IN_EDITOR_WRITES = frozenset({"cost_price"})


def seed_editor_cost(db, product_id: str, cost, actor: Optional[str]) -> Optional[dict]:
    """A first cost typed in the product or catalogue editor: seeded (the
    editors never write a cost with a plain update -- it would overwrite a
    bill landing between their check and their write)."""
    return seed(db, product_id, cost, key=f"editor:{uuid.uuid4().hex}", kind="EDITOR_FIRST_COST", actor=actor)


def editor_first_cost(product: Optional[dict], sent) -> Optional[float]:
    """The first cost an editor save seeds (seed_editor_cost), or None: no
    figure sent, the product already has an average, or the figure is its
    catalogue-only price sent back unchanged (the editor sends the stored
    price on every save -- a price typed only to finish cataloguing stays one)."""
    product = product or {}
    if sent is None or stock_value.average_cost(product):
        return None
    if product.get("cost_price_is_catalogue_only") and _paise(_d(sent)) == _paise(_d(product.get("cost_price"))):
        return None
    return sent


def born_with_cost(doc: dict, *, source: str, actor: Optional[str]) -> None:
    """A product CREATED with a cost (the create doors, clone-vary, promote):
    that cost is its first average, its history row written into the insert
    itself (one document: nothing can land in between). A provisional
    (ordered, not yet catalogued) product's PO rate is only a provisional
    cost: no row, so the receipt's rate can still replace it
    (vendors/gst._promote_cost_from_rate)."""
    first = _paise(_d(doc.get("cost_price")))
    if first <= 0 or doc.get("provisional") or doc.get("cost_price_is_catalogue_only"):
        return
    at, key = utc_now_iso(), f"created:{uuid.uuid4().hex}"
    doc.update({
        "cost_price": first,
        "moving_avg_cost": first,
        "cost_updated_at": at,
        "cost_source": source,
        "cost_source_id": key,
        "cost_keys": [key],
        "cost_history": [{
            "kind": "FIRST_COST", "source": source, "by": actor,
            "key": key, "at": at, "old_cost": None, "new_cost": first,
        }],
    })


def history(product: Optional[dict]) -> List[dict]:
    """The product's cost history, newest first."""
    rows = [dict(r) for r in (product or {}).get("cost_history") or []]
    return sorted(rows, key=lambda r: str(r.get("at") or ""), reverse=True)


EDITOR_COST_REFUSED = (
    "This product already has an average cost, so the product editor cannot change it. "
    "Use 'Correct the average cost' on the product's stock page (Inventory > Stock > the product): "
    "it asks for a reason and goes into the cost history."
)


def refuse_editor_cost(product: Optional[dict], sent) -> None:
    """THE editor-door rule (product editor, catalogue editor): once a
    product has an average, a cost typed there that differs from it is a 409
    pointing to correct() -- one correction door, with a reason and a history
    row. A first cost on a product with none (or the same figure sent back
    unchanged) passes."""
    if sent is None:
        return
    average = stock_value.average_cost(product)
    if average and _paise(_d(sent)) != _paise(_d(average)):
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=EDITOR_COST_REFUSED)
