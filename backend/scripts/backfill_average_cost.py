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
  * --apply: the run id is printed BEFORE the first write; each product's
    before-values are backed up in `backfill_average_cost_backups` first.
    --revert --run-id <id> puts back only products whose cost is still what
    the run wrote (a later bill or correction is reported, never undone).
  A re-run after --apply finds nothing left to fill.

USAGE (dry run is the default; nothing is written without --apply). NEVER run
it on prod without the owner's go-ahead.
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
from api.utils.event_time import utc_now_iso  # noqa: E402

BACKUPS = "backfill_average_cost_backups"
ACTOR = "script:backfill_average_cost"
_FIELDS = ("cost_price", "moving_avg_cost", "cost_price_is_catalogue_only", "cost_valuation_pending",
           "cost_updated_at", "cost_source", "cost_source_id")


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
        fill = None
        reason = "has an average" if average else ""
        if not average:
            if all(own_cost(u) for u in units):
                total = sum(Decimal(str(stock_value._pieces(u))) * Decimal(str(own_cost(u))) for u in units)
                fill = float((total / Decimal(str(pieces))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                reason = "filled from its pieces"
            else:
                reason = "some pieces have no cost: left for a bill or a correction"
        rows.append({
            "product_id": pid, "sku": prod.get("sku"), "pieces": pieces, "average": average or None,
            "value_s4": stock_value.value(pieces, prod), "value_before": round(old_value, 2),
            "fill": fill, "reason": reason,
        })
    return rows


def apply(db, rows: List[dict], run_id: str) -> int:
    done = 0
    for row in rows:
        if row["fill"] is None:
            continue
        prod = db["products"].find_one({"product_id": row["product_id"]}) or {}
        db[BACKUPS].insert_one({
            "run_id": run_id, "product_id": row["product_id"], "at": utc_now_iso(),
            "before": {k: prod.get(k, {"__missing__": True}) for k in _FIELDS},
        })
        written = product_cost.seed(db, row["product_id"], row["fill"], key=f"backfill:{run_id}",
                                    kind="BACKFILL", actor=ACTOR)
        if written:
            db[BACKUPS].update_one({"run_id": run_id, "product_id": row["product_id"]},
                                   {"$set": {"written": written["new_cost"]}})
            done += 1
    return done


def revert(db, run_id: str) -> int:
    key = f"backfill:{run_id}"
    back = 0
    for b in db[BACKUPS].find({"run_id": run_id, "written": {"$exists": True}}):
        pid = b["product_id"]
        prod = db["products"].find_one({"product_id": pid}) or {}
        if (prod.get("cost_keys") or [])[-1:] != [key] or prod.get("cost_price") != b["written"]:
            print(f"  {pid}: changed since the run (a bill or correction) - left as it is")
            continue
        sets = {k: v for k, v in b["before"].items() if v != {"__missing__": True}}
        unsets = {k: "" for k, v in b["before"].items() if v == {"__missing__": True}}
        update: Dict[str, dict] = {"$pull": {"cost_keys": key, "cost_history": {"key": key}}}
        if sets:
            update["$set"] = sets
        if unsets:
            update["$unset"] = unsets
        db["products"].update_one({"product_id": pid, "cost_price": b["written"]}, update)
        back += 1
    return back


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
              + (f" -> {r['fill']:.2f}" if r["fill"] is not None else ""))
    print(f"total value S4 {sum(r['value_s4'] for r in rows):.2f}, "
          f"before {sum(r['value_before'] for r in rows):.2f}; "
          f"{sum(1 for r in rows if r['fill'] is not None)} product(s) to fill")
    if not args.apply:
        print("dry run - nothing written (add --apply)")
        return 0
    run_id = f"{utc_now_iso()[:19].replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"
    print(f"run id {run_id} (undo: --revert --run-id {run_id})")
    print(f"filled {apply(db, rows, run_id)} product(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
