"""Section 17(5)(h): input credit reversed on written-off stock.

Owner 2026-10-24 ("check yourself"), CA 2026-10-23: goods lost, stolen,
destroyed or written off take back the GST input credit claimed on their
purchase. The reversal is reported in GSTR-3B Table 4(B)(1) of the month of
the write-off, on the GSTIN that claimed the credit.

ONE rule for every write-off door:
  - piece_credit(db, unit): the GST claimed on ONE piece of a unit, read from
    the bill that brought it onto its GSTIN (an inter-company transfer's mirror
    bill, else its goods receipt's purchase bill). None when no credit was
    claimed (no bill yet, an unregistered supplier, a 17(5) tick, credit
    switched off).
  - record_writeoff(db, units, ...): one reversal row per written-off unit in
    `itc_writeoff_reversals`, keyed by the unit (`_id` = stock_id): writing a
    unit off twice, or a retry after a crash, never reverses twice. Returns
    the totals to show on the write-off record.
  - month_reversal(db, gstin, period): what GSTR-3B Table 4(B)(1) adds.

ponytail: per piece = the matching bill lines' heads / their quantity (a
header-only bill: its heads pro rata to the piece's price). A piece written
off, then returned to the supplier who issues a credit note, would reverse
again on the note; a damaged piece later restocked keeps its reversal. Add a
re-claim / note offset if either ever happens in practice.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

from ..utils.ist import ist_date_str

COLLECTION = "itc_writeoff_reversals"
_HEADS = ("igst", "cgst", "sgst")
_DEAD = ("CANCELLED", "VOID", "VOIDED")


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _receipt_id(unit: dict) -> Optional[str]:
    if unit.get("grn_id"):
        return unit["grn_id"]
    if unit.get("source_type") == "GRN":
        return unit.get("source_id")
    return None


def _bills_for(db, unit: dict) -> List[dict]:
    """The live bills that brought this unit onto its current GSTIN."""
    bills = db.get_collection("vendor_bills")
    live = {"status": {"$nin": [*_DEAD, *(s.lower() for s in _DEAD)]}}
    if unit.get("transfer_id"):
        mirror = list(bills.find({**live, "source_transfer_id": unit["transfer_id"]}))
        if mirror:
            return mirror
    grn_id = _receipt_id(unit)
    if not grn_id:
        return []
    return list(bills.find({**live, "$or": [{"grn_id": grn_id}, {"linked_dc_ids": grn_id}]}))


def piece_credit(db, unit: dict) -> Optional[dict]:
    """The GST input credit claimed on ONE piece of `unit`:
    {bill_id, gstin, entity_id, igst, cgst, sgst}, or None when none was."""
    from ..routers.finance.gst import _itc_eligible_bill  # THE per-bill credit rule

    pid = unit.get("product_id")
    qty = 0.0
    heads = dict.fromkeys(_HEADS, 0.0)
    claimed = None
    for bill in _bills_for(db, unit):
        claims = _itc_eligible_bill(bill)
        lines = [ln for ln in bill.get("lines") or [] if ln.get("product_id") == pid]
        if lines:
            for ln in lines:
                qty += _f(ln.get("qty"))
                if claims:
                    for h in _HEADS:
                        heads[h] += _f(ln.get(h))
        elif not bill.get("lines") and _f(bill.get("taxable_amount")) > 0:
            # A header-only bill: its GST pro rata to this piece's price.
            price = _f(unit.get("unit_cost")) or _f(unit.get("cost_price"))
            share = price / _f(bill.get("taxable_amount"))
            qty += 1
            if claims:
                for h in _HEADS:
                    heads[h] += _f(bill.get(f"{h}_total")) * share
        else:
            continue
        if claims and claimed is None:
            claimed = bill
    if claimed is None or qty <= 0:
        return None
    per = {h: round(heads[h] / qty, 2) for h in _HEADS}
    if not any(per.values()):
        return None
    return {
        "bill_id": claimed.get("bill_id"),
        "gstin": claimed.get("recipient_gstin") or "",
        "entity_id": claimed.get("recipient_entity_id") or claimed.get("entity_id"),
        **per,
    }


def _pieces(unit: dict) -> float:
    return 1.0 if unit.get("quantity") is None else _f(unit.get("quantity"))


def _period(when) -> str:
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    return ist_date_str(when)[:7]


def totals(rows: Iterable[dict]) -> dict:
    out = dict.fromkeys(_HEADS, 0.0)
    for r in rows:
        for h in _HEADS:
            out[h] = round(out[h] + _f(r.get(h)), 2)
    out["total"] = round(sum(out[h] for h in _HEADS), 2)
    return out


def record_writeoff(db, units: Iterable[dict], *, source_type: str, source_id: str,
                    when, actor_id: str = "") -> dict:
    """Reverse the credit claimed on these written-off units (once per unit,
    ever) and return the totals of their reversal rows -- the first record's
    figures when a unit was already reversed. A unit that claimed no credit
    reverses nothing. A failed write raises: a write-off must not lose its
    GST reversal quietly; re-running this is safe."""
    coll = db.get_collection(COLLECTION)
    period = _period(when)
    ids = []
    for unit in units:
        sid = str(unit.get("stock_id") or unit.get("_id"))
        credit = piece_credit(db, unit)
        if credit is None:
            continue
        n = _pieces(unit)
        coll.update_one(
            {"_id": sid},
            {"$setOnInsert": {
                "stock_id": sid, "product_id": unit.get("product_id"),
                "store_id": unit.get("store_id"), "pieces": n,
                "bill_id": credit["bill_id"], "gstin": credit["gstin"],
                "entity_id": credit["entity_id"], "period": period,
                **{h: round(credit[h] * n, 2) for h in _HEADS},
                "source_type": source_type, "source_id": source_id,
                "recorded_by": actor_id, "recorded_at": datetime.utcnow(),
            }},
            upsert=True,
        )
        ids.append(sid)
    return totals(coll.find({"_id": {"$in": ids}}) if ids else [])


def month_reversal(db, gstin: str, period: str) -> dict:
    """GSTR-3B Table 4(B)(1), section 17(5)(h): the credit reversed on stock
    this GSTIN wrote off in `period` (YYYY-MM)."""
    if not gstin:
        return totals([])
    return totals(db.get_collection(COLLECTION).find({"gstin": gstin, "period": period}))
