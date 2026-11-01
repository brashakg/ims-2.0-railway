"""Give a product its first average cost from its pieces (S4 backfill).

WHY
---
S4 (owner 2026-10-23): every piece of a product carries the product's ONE
average cost (stock_value.average_cost); a piece's own receipt price is no
longer its value. Before S4 a piece could carry a cost of its own while its
product had none (or only a price typed to finish cataloguing): such pieces
would now read as "no cost yet".

WHAT IT DOES
------------
  * Lists every product holding pieces (shelf, reserved, on the way) with: its
    pieces, its average cost, the stock value under S4 (pieces x average) and
    under the old rule (each piece's own cost, else the product's) -- so the
    accountant sees every figure that moves.
  * A product with NO average yet whose held pieces ALL carry a cost of their
    own gets their weighted average as its first average (to the paisa), a
    'BACKFILL' row in its cost history. Pieces without a cost of their own,
    or a product that already has an average, are listed and left alone.
  * The averages follow the owner's rulings of 2026-11-01:
      (a) only the pieces still HELD are averaged -- a sold piece never
          weighs in (it left at the cost its sale line froze, so no
          cost-of-goods variance is owed for a sale before S4);
      (b) a walk-in (no-PO) receipt's pieces are averaged in at their price
          like every other receipt's -- no "latest price paid" replaces the
          average.
  * A product whose average is a pre-S4 WALK-IN latest price (from
    2026-10-12 main set the price paid at the walk-in RECEIPT, cost_source
    GRN_WALKIN, and at its bill -- the bill its cost names, or any accepted
    walk-in bill since then that a later bill blended over) is re-averaged
    from its held pieces' own costs under (a) and (b), a 'BACKFILL' row
    (backed up, revert puts the old average back); if a piece has no cost it
    is flagged to check by hand instead. Only while its average is still
    pre-S4 (no history row): a re-run after an S4 bill never touches it.
  * A product that already has an average but an EMPTY cost history (its
    average is from before S4) gets one 'Carried over' row (kind
    CARRIED_OVER: old none, new = its average, source = its cost_source) so
    the product page never shows an average with "no bill yet". The average
    itself is not changed. A PROVISIONAL cost (a PO or receipt rate,
    product_cost.provisional) is NOT carried over: it stays provisional, so
    its first real bill replaces it instead of blending with it -- unless a
    pre-S4 bill was ACCEPTED on a held piece's receipt (a GST-claiming header
    bill on a PO receipt left the PO rate as the cost): that cost is billed,
    so it IS carried over and the first S4 bill blends with it.
  * Every accepted goods receipt that no S4 bill has costed is stamped
    `costed_before_s4: <run id>` (product_cost.PRE_S4_STAMP; only receipts
    accepted before the first S4 bill -- run it right after the deploy), with
    the time and, per product, the figure its pieces are in the pool at: the
    product's average then -- or, for a product this run filled or
    re-averaged from its pieces, that receipt's held pieces' OWN cost (they
    went into the new average at it), with the product's fill time in the
    same write (PRE_S4_AT_BY: a piece sold after the fill was in the pool at
    it). So the first S4 bill BLENDS with them instead of replacing the
    average. A receipt whose bill simply has not come in yet is stamped too:
    its own S4 bill later trues its pieces up from that stamped figure.
  * The dry run FLAGS products whose average came from a bill that is still
    on hold, or from a delivery-challan bill (both could carry a wrong
    pre-S4 average). That list is for accounts to correct by hand on the
    product page -- this script never changes them.

ONE LIVE RUN AT A TIME
----------------------
  * --apply prints the run id BEFORE the first write; every write carries it
    (the backup row, the product's backfill:<id> history row, the receipt
    stamp), and each product's before-values -- and its catalogue copy's
    cost -- are backed up in `backfill_average_cost_backups` first.
  * --apply REFUSES (exit 1) while any run is still in the data: a product
    carrying a backfill:<id> row or a receipt carrying a stamp. There is no
    run on top of a run.
  * --revert --run-id <id> works on the LATEST run only and puts back
    everything it wrote -- averages, history rows, receipt stamps with every
    figure and time -- so the data is exactly as before the run. It works on
    a run a crash cut short (it undoes whatever was written). The one thing
    it never undoes: a product a bill or correction moved since the run
    (reported), and the stamped figure of that product on its receipts (its
    pieces are in that average at it). Such a run stays in the data and
    --apply keeps refusing; leave it there. Once any S4 bill or correction
    has landed since the run, NEVER restore the backup (it would delete that
    bill's average move, its history row and every sale and receipt since)
    and do not revert: accounts correct any product still wrong one by one
    in the product page's correction box. A backup restore is allowed only
    when nothing has been booked since the run -- in practice, right after
    --apply.

RUNBOOK (prod, the lead only, owner's go-ahead)
-----------------------------------------------
  1. dry run (the default; nothing written) -- read the list;
  2. take a Mongo backup;
  3. --apply -- note the run id it prints first;
  4. on ANY failure (a crash, a wrong figure): --revert --run-id <that id>,
     then --apply again. Never re-run --apply on top of a run. Once an S4
     bill or correction has landed since the run: no revert, no backup
     restore -- correct products by hand (see ONE LIVE RUN AT A TIME).

    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/backfill_average_cost.py
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/backfill_average_cost.py --apply
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/backfill_average_cost.py --revert --run-id <id>

Reads MONGO_PUBLIC_URL, else MONGO_URL; database MONGO_DATABASE (default
ims_2_0). Prints no connection string. No emoji (Windows cp1252).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.services import product_cost, stock_value  # noqa: E402
from api.services.ap_engine import GRN_SUBTYPE_NO_PO  # noqa: E402
from api.utils.event_time import utc_now_iso  # noqa: E402

BACKUPS = "backfill_average_cost_backups"
ACTOR = "script:backfill_average_cost"
# Every product field a run writes: a revert puts each back exactly.
_FIELDS = ("cost_price", "moving_avg_cost", "cost_price_is_catalogue_only", "cost_valuation_pending",
           "cost_updated_at", "cost_source", "cost_source_id", "cost_history", "cost_keys")
MISSING = {"__missing__": True}


def own_cost(unit: dict) -> float:
    """The cost a piece carried before S4: an invoice-priced piece's landed
    cost (its price + GST it could not claim), else its receipt price."""
    if unit.get("cost_source") == "PURCHASE_INVOICE" and stock_value._price(unit.get("landed_cost")):
        return stock_value._price(unit.get("landed_cost"))
    return stock_value._price(unit.get("unit_cost")) or stock_value._price(unit.get("cost_price"))


def plan(db) -> List[dict]:
    """One row per product holding pieces: what S4 values it at, what the old
    rule did, and the first average to fill (None = leave it)."""
    held: Dict[str, List[dict]] = {}
    for unit in db["stock_units"].find({"product_id": {"$nin": [None, ""]}}):
        if product_cost._held(unit):
            held.setdefault(unit["product_id"], []).append(unit)
    rows = []
    for pid, units in sorted(held.items()):
        prod = db["products"].find_one({"product_id": pid}) or {}
        average = stock_value.average_cost(prod)
        pieces = sum(stock_value._pieces(u) for u in units)
        old_value = sum(stock_value._pieces(u) * (own_cost(u) or average) for u in units)
        fill = recost = check = None
        reason = "has an average" if average else ""
        mine = _from_pieces(units, pieces)
        # A GRN_WALKIN price looks provisional: it is a latest price only
        # when it overwrote a billed cost (billed_before_s4).
        walk_in = bool(average) and _walk_in_latest_price(db, prod, pid) and (
            not product_cost.provisional(prod) or billed_before_s4(db, units))
        if walk_in and mine is None:
            check = "average is a pre-S4 walk-in latest price and some pieces have no cost"
        elif walk_in and mine != average:
            fill, recost = mine, average
            reason = "pre-S4 walk-in latest price: re-averaged from its held pieces (ruling b)"
        elif product_cost.provisional(prod):
            reason = ("provisional source, but billed before S4: carried over as its average"
                      if billed_before_s4(db, units) else
                      "provisional (a PO / receipt rate): its first bill replaces it")
        elif not average:
            fill = mine
            reason = ("filled from its pieces" if mine is not None
                      else "some pieces have no cost: left for a bill or a correction")
        rows.append({
            "product_id": pid, "sku": prod.get("sku"), "pieces": pieces, "average": average or None,
            "value_s4": stock_value.value(pieces, prod), "value_before": round(old_value, 2),
            "fill": fill, "recost": recost, "reason": reason, "check": check or _suspect(db, prod),
            "own": _own_by_receipt(units) if fill is not None else {},
        })
    return rows


def _from_pieces(units: List[dict], pieces: float) -> Optional[float]:
    """The held pieces' weighted average of their own costs (rulings (a), (b)),
    None when any has none."""
    if not pieces or not all(own_cost(u) for u in units):
        return None
    total = sum(Decimal(str(stock_value._pieces(u))) * Decimal(str(own_cost(u))) for u in units)
    return float((total / Decimal(str(pieces))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _own_by_receipt(units: List[dict]) -> Dict[str, float]:
    """{receipt: its held pieces' own average} -- the very pieces a fill
    averages, so each receipt's pieces are stamped at the cost they went
    into the new average at."""
    by: Dict[str, List[dict]] = {}
    for u in units:
        if product_cost._receipt_of(u):
            by.setdefault(product_cost._receipt_of(u), []).append(u)
    return {r: _from_pieces(us, sum(stock_value._pieces(u) for u in us)) for r, us in by.items()}


def _source_bill(db, prod: dict) -> dict:
    sid = prod.get("cost_source_id")
    if not sid:
        return {}
    return db["vendor_bills"].find_one({"$or": [{"bill_id": sid}, {"invoice_id": sid}]}, {"_id": 0}) or {}


# From this day main costed a walk-in (no-PO) bill at the LATEST price paid.
LATEST_PRICE_FROM = "2026-10-12"


def _first_s4_bill_at(db) -> Optional[str]:
    """When the first S4 bill was booked (None: none yet)."""
    return next(iter(db["vendor_bills"].find({"cost_state": {"$ne": None}}, {"_id": 0, "created_at": 1})
                     .sort("created_at", 1).limit(1)), {}).get("created_at")


def _walk_in_latest_price(db, prod: dict, pid: str) -> bool:
    """Main set a pre-S4 average at a walk-in (no-PO) buy's LATEST price, not
    averaged (ruling (b) reverses it): at RECEIPT (cost_source GRN_WALKIN --
    any accepted walk-in receipt of the product from 2026-10-12, billed or
    not) or at its bill (the bill its cost names, or any accepted pre-S4 bill
    booked from 2026-10-12 on a walk-in receipt of the product). A later bill
    may have blended over that price, naming itself instead. Only while the
    average is still pre-S4: any history row (a bill, a correction, a run)
    means S4 already owns it."""
    from api.routers.vendors.grn_accept_lock import _GRN_TERMINAL_ACCEPT_STATUSES

    if prod.get("cost_history") or prod.get("cost_source") not in ("PURCHASE_INVOICE", "GRN_WALKIN"):
        return False
    gid = _source_bill(db, prod).get("grn_id")
    receipt = db["grns"].find_one({"grn_id": gid}, {"_id": 0, "grn_subtype": 1}) if gid else None
    if (receipt or {}).get("grn_subtype") == GRN_SUBTYPE_NO_PO:
        return True
    first = str(_first_s4_bill_at(db) or "")
    walk_ins = list(db["grns"].find({"grn_subtype": GRN_SUBTYPE_NO_PO, "items.product_id": pid},
                                    {"_id": 0, "grn_id": 1, "status": 1, "accepted_at": 1}))
    if any(g.get("status") in _GRN_TERMINAL_ACCEPT_STATUSES
           and LATEST_PRICE_FROM <= str(g.get("accepted_at") or "") and (not first or str(g["accepted_at"]) < first)
           for g in walk_ins):
        return True
    walk_ins = [g["grn_id"] for g in walk_ins]
    return bool(walk_ins) and any(
        b.get("cost_state") is None and product_cost.bill_accepted(b)
        and str(b.get("created_at") or "") >= LATEST_PRICE_FROM
        for b in db["vendor_bills"].find({"grn_id": {"$in": walk_ins}}, {"_id": 0})
    )


def billed_before_s4(db, units: List[dict]) -> bool:
    """A held piece's receipt carries an ACCEPTED pre-S4 bill (no cost_state):
    before S4 a GST-claiming header bill on a PO receipt left the PO rate as
    the cost (no true-up), so that provisional-looking cost IS billed."""
    ids = list({product_cost._receipt_of(u) for u in units} - {None})
    if not ids:
        return False
    return any(
        b.get("cost_state") is None and product_cost.bill_accepted(b)
        for b in db["vendor_bills"].find({"$or": [{"grn_id": {"$in": ids}}, {"linked_dc_ids": {"$in": ids}}]},
                                         {"_id": 0})
    )


def _suspect(db, prod: dict) -> Optional[str]:
    """Why accounts should check this product's pre-S4 average by hand, or
    None: it came from a bill still on hold, or from a delivery-challan bill
    (the old rule counted a challan's pieces twice)."""
    bill_id = prod.get("cost_source_id")
    bill = _source_bill(db, prod)
    if bill.get("match_status") == "ON_HOLD_EXCEPTION":
        return f"average from bill {bill.get('bill_number') or bill_id}, still on hold"
    if bill.get("linked_dc_ids"):
        return f"average from delivery-challan bill {bill.get('bill_number') or bill_id}"
    return None


def carry_over_candidates(db) -> List[dict]:
    """Products with an average but no cost history (an average from before
    S4) -- a provisional-source one too when a pre-S4 bill priced it
    (billed_before_s4)."""
    out = []
    for p in db["products"].find({"cost_price": {"$gt": 0}}, {"_id": 0}):
        if p.get("cost_history"):
            continue
        if product_cost.average(p) or (product_cost.provisional(p) and billed_before_s4(db, [
                u for u in db["stock_units"].find({"product_id": p.get("product_id")}) if product_cost._held(u)])):
            out.append(p)
    return out


def legacy_receipts(db) -> List[str]:
    """Accepted goods receipts no S4 bill has costed (and not yet stamped),
    accepted before the first S4 bill was booked (a later one is an S4
    receipt waiting for its bill). Run the backfill right after the deploy."""
    from api.routers.vendors.grn_accept_lock import _GRN_TERMINAL_ACCEPT_STATUSES

    first = _first_s4_bill_at(db)
    out = []
    for g in db["grns"].find({"status": {"$in": list(_GRN_TERMINAL_ACCEPT_STATUSES)},
                              product_cost.PRE_S4_STAMP: {"$exists": False}},
                             {"_id": 0, "grn_id": 1, "accepted_at": 1}):
        gid = g.get("grn_id")
        if first and str(g.get("accepted_at") or "") >= str(first):
            continue
        if gid and not db["vendor_bills"].find_one({
            "cost_state": {"$ne": None},
            "$or": [{"grn_id": gid}, {"linked_dc_ids": gid}],
        }):
            out.append(gid)
    return out


def live_runs(db) -> List[str]:
    """The runs whose writes are still in the data: a product carrying a
    run's key (backfill:<id>: its BACKFILL or Carried-over row) or a receipt
    carrying its stamp. A run a revert could only partly undo (a bill or
    correction moved a product since) stays here for good."""
    runs = {k[len("backfill:"):] for p in db["products"].find({}, {"_id": 0, "cost_keys": 1})
            for k in p.get("cost_keys") or [] if str(k).startswith("backfill:")}
    runs |= {g[product_cost.PRE_S4_STAMP] for g in db["grns"].find(
        {product_cost.PRE_S4_STAMP: {"$exists": True}}, {"_id": 0, product_cost.PRE_S4_STAMP: 1})}
    return sorted(runs)


def _twins(db, prod: dict) -> List[dict]:
    """The catalogue copies a cost write mirrors to (mirror_update_to_catalog_twin)."""
    pid = prod.get("product_id")
    ids = [i for i in {prod.get("pim_product_id"), pid} if i]
    return list(db["catalog_products"].find({"$or": [{"id": {"$in": ids}}, {"spine_product_id": pid}]},
                                            {"_id": 1, "pricing": 1, "needs_review": 1}))


def _backup(db, run_id: str, pid: str) -> None:
    prod = db["products"].find_one({"product_id": pid}) or {}
    db[BACKUPS].insert_one({
        "run_id": run_id, "product_id": pid, "at": utc_now_iso(),
        "before": {k: prod.get(k, MISSING) for k in _FIELDS},
        "twins": [{"_id": t["_id"], "cost": (t.get("pricing") or {}).get("cost_price", MISSING),
                   "needs_review": t.get("needs_review", MISSING)} for t in _twins(db, prod)],
    })


def carry_over(db, prod: dict, key: str) -> Optional[dict]:
    """One 'Carried over' history row on a product whose average has no
    history; the average is not changed. One guarded single-document update
    (on the average and the empty history): a re-run, or a bill landing
    meanwhile, is never overwritten."""
    row = {"kind": "CARRIED_OVER", "source": prod.get("cost_source"), "key": key, "at": utc_now_iso(),
           "old_cost": None, "new_cost": stock_value.average_cost(prod), "by": ACTOR}
    done = db["products"].update_one(
        {"product_id": prod["product_id"], "cost_price": prod.get("cost_price"),
         "cost_history": {"$in": [None, []]}, "cost_keys": {"$ne": key}},
        {"$push": {"cost_history": row, "cost_keys": key}},
    )
    return row if done.modified_count else None


def _recost(db, pid: str, cost: float, expected: float, key: str) -> Optional[dict]:
    """Re-average a pre-S4 walk-in latest price (one guarded write, only while
    the average is still the one the dry run read)."""
    return product_cost._move(
        db, pid, key,
        lambda _prod, old: (cost, {}) if product_cost._paise(product_cost._d(old)) == expected else None,
        {"kind": "BACKFILL", "source": "BACKFILL", "by": ACTOR,
         "reason": "pre-S4 walk-in latest price re-averaged (owner 2026-11-01 (b))"},
    )


def apply(db, rows: List[dict], run_id: str) -> int:
    """One run. Refuses while any earlier run is still in the data (never a
    run on top: after a crash, --revert that run, then --apply again). Every
    write carries the run id, so a run a crash cut short reverts in full."""
    live = live_runs(db)
    if live:
        sys.exit(f"refused: run {', '.join(live)} is still applied. Revert it first "
                 f"(--revert --run-id {live[-1]}), then apply again - never run on top of it.")
    key = f"backfill:{run_id}"
    done = 0
    filled: Dict[str, str] = {}  # product filled or re-averaged from its pieces -> its fill time
    for row in rows:
        if row["fill"] is None:
            continue
        _backup(db, run_id, row["product_id"])
        if row.get("recost") is not None:
            written = _recost(db, row["product_id"], row["fill"], row["recost"], key)
        else:
            written = product_cost.seed(db, row["product_id"], row["fill"], key=key, kind="BACKFILL", actor=ACTOR)
        if written:
            db[BACKUPS].update_one({"run_id": run_id, "product_id": row["product_id"]},
                                   {"$set": {"written": written["new_cost"]}})
            filled[row["product_id"]] = written["at"]
            done += 1
    for prod in carry_over_candidates(db):
        _backup(db, run_id, prod["product_id"])
        written = carry_over(db, prod, key)
        if written:
            db[BACKUPS].update_one({"run_id": run_id, "product_id": prod["product_id"]},
                                   {"$set": {"written": written["new_cost"]}})
            done += 1
    own = {row["product_id"]: row.get("own") or {} for row in rows}
    at = utc_now_iso()
    for gid in legacy_receipts(db):
        receipt = db["grns"].find_one({"grn_id": gid}, {"_id": 0, "items": 1}) or {}
        pids = {it.get("product_id") for it in receipt.get("items") or [] if it.get("product_id")}
        # A product filled from its pieces holds this receipt's pieces at their
        # OWN cost, in the pool from its fill time; any other at its average.
        figures = {p["product_id"]: product_cost.average(p)
                   for p in db["products"].find({"product_id": {"$in": list(pids - set(filled))}})
                   if product_cost.average(p)}
        mine = {p: own[p][gid] for p in pids & set(filled) if gid in own.get(p, {})}
        figures.update(mine)
        db["grns"].update_one({"grn_id": gid, product_cost.PRE_S4_STAMP: {"$exists": False}},
                              {"$set": {product_cost.PRE_S4_STAMP: run_id, product_cost.PRE_S4_AT: at,
                                        product_cost.PRE_S4_AVERAGES: figures,
                                        product_cost.PRE_S4_AT_BY: {p: filled[p] for p in mine}}})
    return done


def revert(db, run_id: str) -> int:
    """Put back everything run `run_id` wrote -- the latest run only, applied
    in full or cut short by a crash -- except a product a bill or correction
    moved since (reported, never undone) and that product's receipt figures."""
    live = live_runs(db)
    if live and run_id not in live:
        sys.exit(f"refused: {run_id} is not the latest run - only run {', '.join(live)} can be reverted")
    key = f"backfill:{run_id}"
    back = 0
    for b in db[BACKUPS].find({"run_id": run_id}):
        pid = b["product_id"]
        prod = db["products"].find_one({"product_id": pid}) or {}
        ours = [r for r in prod.get("cost_history") or [] if r.get("key") == key]
        if not ours:
            continue  # the run never wrote it (or a revert already put it back)
        written = b.get("written", ours[0].get("new_cost"))  # a crash can land before "written"
        if (prod.get("cost_keys") or [])[-1:] != [key] or prod.get("cost_price") != written:
            print(f"  {pid}: changed since the run (a bill or correction) - left as it is")
            continue
        sets = {k: v for k, v in b["before"].items() if v != MISSING}
        unsets = {k: "" for k, v in b["before"].items() if v == MISSING}
        update: Dict[str, dict] = {}
        if sets:
            update["$set"] = sets
        if unsets:
            update["$unset"] = unsets
        db["products"].update_one({"product_id": pid, "cost_price": written, "cost_keys": prod["cost_keys"]}, update)
        for t in b.get("twins") or []:
            twin = db["catalog_products"].find_one({"_id": t["_id"]}) or {}
            if (twin.get("pricing") or {}).get("cost_price") == written and t["cost"] != written:
                # ponytail: a twin with no pricing before keeps an empty one
                db["catalog_products"].update_one(
                    {"_id": t["_id"]}, {"$unset": {"pricing.cost_price": ""}} if t["cost"] == MISSING
                    else {"$set": {"pricing.cost_price": t["cost"]}})
            if t["needs_review"] is True and twin.get("needs_review") is False:
                db["catalog_products"].update_one({"_id": t["_id"]}, {"$set": {"needs_review": True}})
        back += 1
    _unstamp(db, run_id, key)
    return back


def _unstamp(db, run_id: str, key: str) -> None:
    """Take the run's stamp off its receipts -- but never off one carrying a
    product whose average moved after the stamp (a bill or correction, not
    this run): its pieces are in that average at the stamped figure, so
    their own bill must still find the stamp. Such a receipt keeps the
    stamped figure of the moved products only: every other product's entry
    goes (revert put it back, so its pieces wait for their bill)."""
    for g in db["grns"].find({product_cost.PRE_S4_STAMP: run_id}, {"_id": 0}):
        at = str(g.get(product_cost.PRE_S4_AT) or "")
        pids = [it.get("product_id") for it in g.get("items") or [] if it.get("product_id")]
        moved = sorted(
            p["product_id"] for p in db["products"].find({"product_id": {"$in": pids}})
            if any(r.get("key") != key and str(r.get("at") or "") >= at for r in p.get("cost_history") or [])
        )
        if moved:
            print(f"  receipt {g['grn_id']}: still marked costed before S4 - {', '.join(moved)} moved since the run")
            drop = {f"{f}.{p}": "" for p in g.get(product_cost.PRE_S4_AVERAGES) or {} if p not in moved
                    for f in (product_cost.PRE_S4_AVERAGES, product_cost.PRE_S4_AT_BY)}
            if drop:
                db["grns"].update_one({"grn_id": g["grn_id"], product_cost.PRE_S4_STAMP: run_id}, {"$unset": drop})
            continue
        db["grns"].update_one({"grn_id": g["grn_id"], product_cost.PRE_S4_STAMP: run_id},
                              {"$unset": {product_cost.PRE_S4_STAMP: "", product_cost.PRE_S4_AT: "",
                                          product_cost.PRE_S4_AVERAGES: "", product_cost.PRE_S4_AT_BY: ""}})


def _db():
    from pymongo import MongoClient

    url = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGO_URL")
    if not url:
        sys.exit("MONGO_PUBLIC_URL / MONGO_URL not set")
    return MongoClient(url)[os.environ.get("MONGO_DATABASE", "ims_2_0")]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--run-id")
    args = ap.parse_args(argv)
    db = _db()
    if args.revert:
        if not args.run_id:
            sys.exit("--revert needs --run-id")
        print(f"reverted {revert(db, args.run_id)} product(s) of run {args.run_id}")
        return 0
    rows = plan(db)
    for r in rows:
        print(f"{r['sku'] or r['product_id']}: {r['pieces']:g} pcs, average {r['average']}, "
              f"value S4 {r['value_s4']:.2f} (before {r['value_before']:.2f}) - {r['reason']}"
              + (f" -> {r['fill']:.2f}" if r["fill"] is not None else "")
              + (f"  CHECK BY HAND: {r['check']}" if r.get("check") else ""))
    print("averages under the owner's 2026-11-01 rulings: (a) held pieces only, never sold ones; "
          "(b) walk-in pieces averaged in like any receipt's")
    print(f"total value S4 {sum(r['value_s4'] for r in rows):.2f}, "
          f"before {sum(r['value_before'] for r in rows):.2f}; "
          f"{sum(1 for r in rows if r['fill'] is not None)} product(s) to fill or re-average; "
          f"{len(carry_over_candidates(db))} average(s) to carry over into the history; "
          f"{len(legacy_receipts(db))} receipt(s) to mark as costed before S4; "
          f"{sum(1 for r in rows if r.get('check'))} product(s) for accounts to check by hand")
    if not args.apply:
        print("dry run - nothing written (add --apply)")
        return 0
    run_id = f"{utc_now_iso()[:19].replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"
    print(f"run id {run_id} (undo: --revert --run-id {run_id})")
    print(f"filled {apply(db, rows, run_id)} product(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
