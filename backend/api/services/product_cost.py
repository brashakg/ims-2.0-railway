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
    `basis` = the pieces we hold (shelf, reserved, on the way between shops)
    that a bill has already costed. Pieces of a receipt that still has
    unbilled pieces -- this bill's own receipt or delivery challans among
    them -- are left out: they carry the average only provisionally until
    their own bill, so a challan's pieces are never counted twice. Rejected
    pieces never count (no more pieces than the receipts accepted). A product
    with no average yet takes the bill's price. A walk-in buy keeps the
    latest price paid (owner 2026-10-12).
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
from typing import Callable, Dict, Iterable, List, Optional, Tuple

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


def _held(unit: dict) -> bool:
    """A piece we still hold: on the shelf, reserved, or on the way."""
    raw = unit.get("status")
    return is_on_hand(raw, include_reserved=True) or canonical_state(raw) is StockState.TRANSFERRED


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
    latest_price: bool = False,
    receipts: Optional[List[dict]] = None,
    keep: Iterable[str] = (),
) -> List[dict]:
    """Re-average every product an accepted bill's goods lines name. `lines`
    are its COST lines (GST it cannot claim already added:
    purchase_invoices._cost_lines). `keep`: products the bill names whose
    average it must leave as it is (a walk-in buy a later receipt has
    outpriced). Every product the bill names ends up holding its key, moved
    or not (mark), so its receipt's pieces join the basis. Returns the
    history rows written."""
    for pid in keep:
        mark(db, pid, key)
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
            if latest_price or not old:
                return price, {"qty": float(qty), "price": price, "basis": None}
            basis = Decimal(str(basis_pieces(db, pid, prod.get("cost_keys") or ())))
            if basis <= 0:
                return price, {"qty": float(qty), "price": price, "basis": 0.0}
            new = (basis * Decimal(str(old)) + val) / (basis + qty)
            return _paise(new), {"qty": float(qty), "price": price, "basis": float(basis)}

        row = _move(db, pid, key, work, {
            "kind": "BILL",
            "source": "PURCHASE_INVOICE",
            "bill_id": bill.get("invoice_id") or bill.get("bill_id"),
            "bill_number": bill.get("invoice_number") or bill.get("bill_number"),
            "vendor_name": bill.get("vendor_name"),
            "method": "LATEST_PRICE_PAID" if latest_price else "AVERAGE",
            "by": actor,
        })
        if row:
            rows.append({**row, "product_id": pid})
    return rows


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
    (a product that no longer exists counts as done) -- THE test before a
    bill is marked APPLIED, so a failed or half-done apply stays PENDING for
    the sweeper."""
    pids = billed_products(lines)
    if not pids:
        return True
    found = {
        p["product_id"]: p.get("cost_keys") or []
        for p in db.get_collection("products").find({"product_id": {"$in": pids}}, {"product_id": 1, "cost_keys": 1})
    }
    return all(key in keys for keys in found.values())


def correct(db, product_id: str, new_cost: float, reason: str, actor: Optional[str]) -> Optional[dict]:
    """An accounts correction of a product's average, with its reason."""
    new = _paise(_d(new_cost))
    return _move(
        db, product_id, f"correction:{uuid.uuid4().hex}",
        lambda _prod, old: None if new == old else (new, {}),
        {"kind": "CORRECTION", "source": "CORRECTION", "reason": reason, "by": actor},
    )


def seed(db, product_id: str, cost: float, *, key: str, kind: str, actor: Optional[str]) -> Optional[dict]:
    """A product's FIRST cost (opening stock; the backfill): written only
    while it has none."""
    first = _paise(_d(cost))
    return _move(
        db, product_id, key,
        lambda _prod, old: None if old or first <= 0 else (first, {}),
        {"kind": kind, "source": kind, "by": actor},
    )


# Never in an editor door's plain write (seed_editor_cost writes a first cost).
NOT_IN_EDITOR_WRITES = frozenset({"cost_price"})


def seed_editor_cost(db, product_id: str, cost, actor: Optional[str]) -> Optional[dict]:
    """A first cost typed in the product or catalogue editor: seeded (the
    editors never write a cost with a plain update -- it would overwrite a
    bill landing between their check and their write)."""
    return seed(db, product_id, cost, key=f"editor:{uuid.uuid4().hex}", kind="EDITOR_FIRST_COST", actor=actor)


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
