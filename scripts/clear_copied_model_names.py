#!/usr/bin/env python3
"""
IMS 2.0 - Blank the Model Names the system copied from the Model Number
=======================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). ONE-TIME.

WHY
---
Until the fix in this PR the create door (product_master.normalise_door_payload)
filled EVERY model field a category's form shows from the form's flat `model`
(its Model No), and the clone overlay (_overlay_attributes, "add another
colour") did the same. So a frame / sunglass saved with a blank Model Name got
its Model Number copied in: 583 of 603 prod Model Names equal the number.
Staff confirm they typed none of them (owner 2026-10-19), so every such copy
is blanked.

WHAT IT TOUCHES
---------------
Only categories whose form shows BOTH a Model No (required) and an optional
Model Name -- FRAME and SUNGLASS (read off product_master's registry, not a
hand list). A contact lens / smartglass whose Model Name is its REQUIRED key
is never touched. A row matches when attributes.model_name equals
attributes.model_no, ignoring case and outer spaces. It $unsets
attributes.model_name on the spine (`products`) and on its catalogue twin
(`catalog_products`, id = pim_product_id or product_id) when the twin's copy
is the same value. Nothing else moves: no SKU, name, identity key, price or
Shopify queue flag. Product names / website titles never showed the copy
(product_naming._model_pieces skips a model name already inside the number).

Every write is backed up first into `model_name_copy_backups` under a run id;
--revert --run-id X puts each value back (only where the field is still blank).

USAGE
-----
Dry run (DEFAULT - counts and lists, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/clear_copied_model_names.py
Apply:
    ... scripts/clear_copied_model_names.py --apply
Undo:
    ... scripts/clear_copied_model_names.py --revert --run-id <id>

Connection: --mongo-uri, else MONGO_PUBLIC_URL, else MONGO_URL. Plain pymongo,
no app start-up. No secret value is ever printed.
"""

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
os.environ.setdefault("JWT_SECRET_KEY", "clear-copied-model-names")
os.environ.setdefault("ENVIRONMENT", "script")

from api.services.product_master import category_spec, resolve_category  # noqa: E402

PRODUCTS = "products"
TWINS = "catalog_products"
BACKUPS = "model_name_copy_backups"


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _optional_model_name(category: Any) -> bool:
    """True for a category whose form shows a required Model No and an
    OPTIONAL Model Name (a frame, a sunglass)."""
    spec = category_spec(category)
    return bool(spec) and "model_no" in spec.required and "model_name" in spec.optional


def plan(db) -> List[Dict[str, Any]]:
    """Every spine row whose Model Name is a copy of its Model Number."""
    rows = []
    for doc in db[PRODUCTS].find(
        {"attributes.model_name": {"$nin": [None, ""]}},
        {"product_id": 1, "pim_product_id": 1, "sku": 1, "category": 1, "attributes": 1},
    ):
        a = doc.get("attributes") or {}
        if not _norm(a.get("model_no")) or _norm(a.get("model_name")) != _norm(a.get("model_no")):
            continue
        if not _optional_model_name(resolve_category(doc.get("category")) or doc.get("category")):
            continue
        rows.append({
            "product_id": doc.get("product_id"),
            "twin_id": doc.get("pim_product_id") or doc.get("product_id"),
            "sku": doc.get("sku"),
            "category": doc.get("category"),
            "model_no": a.get("model_no"),
            "model_name": a.get("model_name"),
        })
    return rows


def apply(db, rows: List[Dict[str, Any]], run_id: str) -> int:
    """Back up, then blank. Returns the number of spine rows blanked."""
    now = datetime.now(timezone.utc)
    done = 0
    for r in rows:
        twin = db[TWINS].find_one({"id": r["twin_id"]}, {"attributes.model_name": 1}) or {}
        twin_value = (twin.get("attributes") or {}).get("model_name")
        twin_too = twin_value is not None and _norm(twin_value) == _norm(r["model_name"])
        db[BACKUPS].insert_one({
            **r, "run_id": run_id, "at": now,
            "twin_model_name": twin_value if twin_too else None,
        })
        res = db[PRODUCTS].update_one(
            {"product_id": r["product_id"], "attributes.model_name": r["model_name"]},
            {"$unset": {"attributes.model_name": ""}},
        )
        done += res.modified_count
        if twin_too:
            db[TWINS].update_one(
                {"id": r["twin_id"], "attributes.model_name": twin_value},
                {"$unset": {"attributes.model_name": ""}},
            )
    return done


def revert(db, run_id: str) -> int:
    """Put each backed-up Model Name back where it is still blank."""
    blank = {"$in": [None, ""]}
    n = 0
    for b in db[BACKUPS].find({"run_id": run_id}):
        res = db[PRODUCTS].update_one(
            {"product_id": b["product_id"], "attributes.model_name": blank},
            {"$set": {"attributes.model_name": b["model_name"]}},
        )
        n += res.modified_count
        if b.get("twin_model_name") is not None:
            db[TWINS].update_one(
                {"id": b["twin_id"], "attributes.model_name": blank},
                {"$set": {"attributes.model_name": b["twin_model_name"]}},
            )
    return n


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Blank copied Model Names. Dry run by default.")
    ap.add_argument("--mongo-uri", default=None)
    ap.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--revert", action="store_true", help="undo a run (needs --run-id)")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args(argv)
    if args.revert and not args.run_id:
        print("[ERROR] --revert needs --run-id")
        return 2
    uri = args.mongo_uri or os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGO_URL")
    if not uri:
        print("[ERROR] no Mongo connection: set MONGO_PUBLIC_URL / MONGO_URL or pass --mongo-uri")
        return 2
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    if args.revert:
        print(f"[REVERTED] run {args.run_id}: {revert(db, args.run_id)} Model Name(s) restored")
        return 0
    rows = plan(db)
    print(f"{len(rows)} product(s) whose Model Name is a copy of the Model Number:")
    for r in rows:
        print(f"  {r['sku']}  [{r['category']}]  model_no={r['model_no']!r}  model_name={r['model_name']!r}")
    if not args.apply:
        print("DRY RUN - nothing written. Re-run with --apply to blank these Model Names.")
        return 0
    run_id = uuid.uuid4().hex[:12]
    print(f"[APPLY] run id {run_id}")
    print(f"[APPLIED] run {run_id}: {apply(db, rows, run_id)} Model Name(s) blanked "
          f"(undo: --revert --run-id {run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
