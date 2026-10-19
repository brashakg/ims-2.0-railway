#!/usr/bin/env python3
"""
IMS 2.0 -- fix website/bill names that print the brand twice
============================================================
Names minted before the brand was stripped from the model number read
"Carrera Carrera 3044/S Sunglasses - Black". The name builder is fixed for new
products and for a re-mint on edit; this script renames the EXISTING rows whose
stored name is exactly what the old formula minted and differs from what the
create door mints today.

It renames only:
  * products.name  (the spine's display name)
  * the catalog twin's name + title, and only when they still equal the old name
It never touches order lines, printed labels, SKUs, barcodes or identity keys,
and never a name somebody typed (a stored name that is not the old minted form).
It does NOT queue a Shopify push: the twin's title is updated, and the next push
of that listing sends it.

SAFETY: DRY RUN by default (prints SKU: before -> after and a count, writes
nothing). --apply writes, after saving every old name in the
`doubled_brand_name_backups` collection under a run id. --revert --run-id X puts
the old names back (only where the name is still the one this run wrote).
Plain pymongo via MONGO_URL; init_db is never called (it may create indexes).
No emoji (Windows cp1252). Prints no secrets.

  python backend/scripts/fix_doubled_brand_names.py                     # dry run
  python backend/scripts/fix_doubled_brand_names.py --apply
  python backend/scripts/fix_doubled_brand_names.py --revert --run-id <id>
  railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/fix_doubled_brand_names.py
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

BACKUPS = "doubled_brand_name_backups"


def plan(db) -> dict:
    """Every product whose stored name is the OLD minted form and differs from
    today's. Pure read. Returns {"rows": [...], "scanned": n, "typed": n}."""
    from api.services.product_master import ProductMasterError, minted_name
    from api.services.product_naming import legacy_product_name

    rows, scanned, typed = [], 0, 0
    for doc in db["products"].find({"name": {"$type": "string", "$ne": ""}}):
        scanned += 1
        name = doc["name"]
        try:
            new = minted_name(doc)
            old_forms = {minted_name(doc, legacy=True), legacy_product_name({**doc, "name": None})}
        except ProductMasterError:
            continue
        if not new or name == new:
            continue
        if name not in old_forms:
            typed += 1  # a typed / different name: never ours to change
            continue
        rows.append({
            "product_id": doc.get("product_id"),
            "sku": doc.get("sku"),
            "twin_id": doc.get("pim_product_id") or doc.get("product_id"),
            "old": name,
            "new": new,
        })
    return {"rows": rows, "scanned": scanned, "typed": typed}


def apply(db, rows: list, run_id: str) -> int:
    """Save the backup first, then rename. Returns the number of products renamed."""
    now = datetime.now(tz=timezone.utc)
    done = 0
    for r in rows:
        twin = db["catalog_products"].find_one({"id": r["twin_id"]}) or {}
        # The twin follows only while it still shows the old name.
        twin_fields = [f for f in ("name", "title") if twin.get(f) == r["old"]]
        db[BACKUPS].insert_one({**r, "run_id": run_id, "twin_fields": twin_fields, "at": now})
        res = db["products"].update_one(
            {"product_id": r["product_id"], "name": r["old"]}, {"$set": {"name": r["new"]}}
        )
        if res.modified_count:
            done += 1
            if twin_fields:
                db["catalog_products"].update_one(
                    {"id": r["twin_id"]}, {"$set": {f: r["new"] for f in twin_fields}}
                )
    return done


def revert(db, run_id: str) -> int:
    """Put a run's old names back, only where the name is still the one it wrote."""
    done = 0
    for b in list(db[BACKUPS].find({"run_id": run_id})):
        res = db["products"].update_one(
            {"product_id": b["product_id"], "name": b["new"]}, {"$set": {"name": b["old"]}}
        )
        if res.modified_count:
            done += 1
            for f in b.get("twin_fields") or []:
                db["catalog_products"].update_one(
                    {"id": b["twin_id"], f: b["new"]}, {"$set": {f: b["old"]}}
                )
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--revert", action="store_true", help="undo a run (needs --run-id)")
    ap.add_argument("--run-id", help="the run id --apply printed")
    args = ap.parse_args()
    if args.revert and not args.run_id:
        print("[ERROR] --revert needs --run-id")
        return 2
    url = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGO_URL") or os.getenv("MONGODB_URL")
    print(f"[MONGO] connection string: {'SET' if url else 'NOT SET'}")
    if not url:
        return 1
    try:
        from pymongo import MongoClient

        client = MongoClient(url, serverSelectionTimeoutMS=20_000)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        print(f"[MONGO] connect failed: {type(exc).__name__}")
        return 1
    db = client[os.getenv("MONGO_DATABASE", "ims_2_0")]

    if args.revert:
        print(f"[REVERTED] run {args.run_id}: {revert(db, args.run_id)} product name(s) restored")
        return 0
    out = plan(db)
    for r in out["rows"]:
        print(f"  {r['sku']}: {r['old']} -> {r['new']}")
    print(f"scanned {out['scanned']}, to rename {len(out['rows'])}, typed names left alone {out['typed']}")
    if not args.apply:
        print("[DRY RUN] nothing written. Re-run with --apply to rename.")
        return 0
    run_id = uuid.uuid4().hex[:12]
    print(f"[APPLY] run id {run_id}")
    print(f"[APPLIED] run {run_id}: {apply(db, out['rows'], run_id)} product name(s) renamed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
