#!/usr/bin/env python3
"""
IMS 2.0 - Reverse-charge purchase bills owe the supplier the taxable value
=========================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
A reverse-charge (RCM) bill booked before ap_engine.vendor_payable stored its
GST inside total_amount / total, and its outstanding and status were worked
out against that. The supplier is owed the TAXABLE value only (the GST is the
shop's own to pay the government, GSTR-3B 3.1(d)). The readers that go through
vendor_payable (aging, ledger, supplier balance) read such a bill right, but
the STORED total, outstanding and status stay wrong: a bill whose supplier was
paid the 1000 on his invoice still says PARTIAL with 180 outstanding, and the
invoice list's PAID filter leaves it out.

WHAT IT DOES
------------
For every bill with reverse_charge True whose status is one the AP status
rule writes (OUTSTANDING / PARTIAL / PAID, or none) -- a cancelled or void
bill is never touched -- it stamps ap_engine.bill_settlement, the same rule a
payment's status write uses: total_amount (and total, where the bill has
it) = what the supplier is owed, outstanding and status from the payments and
debit notes allocated to it. A bill that already reads right plans nothing,
so a second run plans nothing. Each write is guarded on the values it read,
so a payment landing between the plan and the write is never overwritten.

USAGE
-----
Dry-run (DEFAULT - prints the plan, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/repair_rcm_bill_payable.py

Apply:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/repair_rcm_bill_payable.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
from api.services.ap_engine import bill_settlement  # noqa: E402  THE rule

# The statuses bill_settlement writes; None also matches a bill with no status.
_SETTLEABLE = [None, "OUTSTANDING", "PARTIAL", "PAID"]


def plan(db) -> List[Dict]:
    """[{bill_id, bill_number, was: {...}, set: {...}}] -- what apply() writes."""
    rows = []
    for bill in db["vendor_bills"].find(
        {"reverse_charge": True, "status": {"$in": _SETTLEABLE}}, {"_id": 0}
    ):
        bid = bill.get("bill_id")
        if not bid:
            continue
        st = bill_settlement(
            bill,
            list(db["vendor_payments"].find({"bill_id": bid}, {"_id": 0})),
            list(db["vendor_debit_notes"].find({"bill_id": bid}, {"_id": 0})),
        )
        want = {
            "total_amount": st["owed"],
            "outstanding": st["outstanding"],
            "status": st["status"],
        }
        if "total" in bill:
            want["total"] = st["owed"]
        change = {k: v for k, v in want.items() if bill.get(k) != v}
        if change:
            rows.append(
                {
                    "bill_id": bid,
                    "bill_number": bill.get("bill_number"),
                    "was": {k: bill.get(k) for k in change},
                    "set": change,
                }
            )
    return rows


def apply(db, rows) -> Dict[str, int]:
    """Write each planned change, only while the bill still reads as planned."""
    written = 0
    for row in rows:
        res = db["vendor_bills"].update_one(
            {"bill_id": row["bill_id"], "reverse_charge": True, **row["was"]},
            {"$set": dict(row["set"])},
        )
        written += res.modified_count
    return {"bills": len(rows), "bills_written": written}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Restamp reverse-charge bills at what the supplier is owed. Dry-run by default."
    )
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true", help="Write. Without it: dry-run.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    rows = plan(db)
    mode = "APPLY" if args.apply else "DRY-RUN"
    for row in rows:
        print(f"[{mode}] {row['bill_id']} ({row['bill_number']}): {row['was']} -> {row['set']}")
    print(f"[{mode}] {len(rows)} reverse-charge bill(s) to restamp")
    if args.apply:
        print(f"[APPLY] {apply(db, rows)}")
    else:
        print("[DRY-RUN] nothing written; re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
