#!/usr/bin/env python3
"""
IMS 2.0 - Queue the live listings whose products carry a weight
================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). ONE-TIME.

WHY
---
Weight (grams) lives on the billing SPINE (`products`). Until the fix in this
PR the Shopify push never sent it, so every live listing shows 0 g on
Shopify. The push now sends each variant's spine weight
(inventoryItem.measurement.weight, GRAMS) on create AND on update -- but
only for a listing that is queued. This script queues the live listings
that have a weight, so the next sync carries it.

Weight is NOT copied onto the catalogue twin: the spine is the one copy
(the push and the catalogue screens read it there). The other way round is
a GUARD: a weight that was saved on a twin alone (the catalogue door did
that before this fix) is copied onto its spine when the spine has none, so
it is never stranded. The spine is found by the LINK (pim_product_id, else
product_id = the twin's id -- catalog._spine_ids), never by SKU. Measured on
prod 2026-10-22: 0 twins carry a weight, so this step has nothing to do
there today.

WHAT IT TOUCHES
---------------
For every spine with weight > 0, the listing that carries it: its own twin,
or -- for a colour/size of a model (spine variant_of) -- its MODEL lead's
twin. Only a twin LIVE on Shopify (ecom.shopify_product_id) that owns a
listing (no ecom.variant_of) and is not switched off, deleted, archived or
taken down (is_active False / deleted_at / status or ecom.status ARCHIVED /
ecom.taken_down_at) is queued: ecom.locally_modified + a fresh
ecom.edit_rev (catalog_ecom.dirty_marks). A never-published product is not
queued here (its first publish follows the normal rules). Nothing else moves.

Each write is backed up into `weight_queue_backups` under a run id; --revert
--run-id X puts the previous queue flag back on every twin whose edit
revision is still the one this run set (a newer edit is never undone), and
removes each copied spine weight that still holds the copied value.

USAGE
-----
Dry run (DEFAULT - counts and SKUs, writes nothing):
    railway run --service ims-2.0-railway '.venv\\Scripts\\python.exe' -I scripts/queue_weight_listings.py
Apply:
    ... scripts/queue_weight_listings.py --apply
Undo:
    ... scripts/queue_weight_listings.py --revert --run-id <id>

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
os.environ.setdefault("JWT_SECRET_KEY", "queue-weight-listings")
os.environ.setdefault("ENVIRONMENT", "script")

from api.services.catalog_ecom import EDIT_REV, dirty_marks  # noqa: E402

PRODUCTS = "products"
TWINS = "catalog_products"
BACKUPS = "weight_queue_backups"


def _w(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _spine_of_twin(twin: Dict[str, Any], by_pim: Dict[Any, Dict], by_pid: Dict[Any, Dict]) -> Optional[Dict]:
    """THE twin -> spine link (catalog._spine_ids): pim_product_id, else
    product_id, equal to the twin's id. Never by SKU."""
    return by_pim.get(twin.get("id")) or by_pid.get(twin.get("id"))


def plan_copies(spines: List[Dict[str, Any]], twins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure: the twin weights with no spine copy -- [{product_id, sku, twin_id,
    weight}] for each linked spine that has no weight while its twin does."""
    by_pim = {s.get("pim_product_id"): s for s in spines if s.get("pim_product_id")}
    by_pid = {s.get("product_id"): s for s in spines if s.get("product_id")}
    out = []
    for t in twins:
        w = _w(t.get("weight"))
        spine = _spine_of_twin(t, by_pim, by_pid) if w > 0 else None
        if spine is not None and _w(spine.get("weight")) <= 0:
            out.append({"product_id": spine["product_id"], "sku": spine.get("sku"),
                        "twin_id": t["id"], "weight": w})
    return out


def _off(twin: Dict[str, Any]) -> bool:
    """Switched off, deleted, archived or taken down: never queued."""
    ecom = twin.get("ecom") or {}
    return (
        twin.get("is_active") is False
        or bool(twin.get("deleted_at"))
        or "ARCHIVED" in (str(twin.get("status") or "").upper(), str(ecom.get("status") or "").upper())
        or bool(ecom.get("taken_down_at"))
    )


def plan(spines: List[Dict[str, Any]], twins: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure: {weighted, queue: [{twin_id, sku, weighted_skus}], not_live}.
    `spines` / `twins` are the two collections' docs."""
    by_pid = {s.get("product_id"): s for s in spines if s.get("product_id")}
    by_id = {t.get("id"): t for t in twins if t.get("id")}
    queue: Dict[Any, Dict[str, Any]] = {}
    not_live: List[str] = []
    weighted = [s for s in spines if _w(s.get("weight")) > 0]
    for s in weighted:
        lead = by_pid.get(s.get("variant_of")) if s.get("variant_of") else s
        twin = None
        for key in ((lead or {}).get("pim_product_id"), (lead or {}).get("product_id")):
            if key in by_id:
                twin = by_id[key]
                break
        ecom = (twin or {}).get("ecom") or {}
        if twin is None or ecom.get("variant_of") or not ecom.get("shopify_product_id") or _off(twin):
            not_live.append(str(s.get("sku")))
            continue
        row = queue.setdefault(
            twin["id"], {"twin_id": twin["id"], "sku": twin.get("sku"), "weighted_skus": []}
        )
        row["weighted_skus"].append(str(s.get("sku")))
    return {"weighted": len(weighted), "queue": list(queue.values()), "not_live": not_live}


def apply_copies(db, rows: List[Dict[str, Any]], run_id: str) -> int:
    """Copy each stranded twin weight onto its spine (only while the spine
    still has none), backed up under the run id."""
    now = datetime.now(timezone.utc)
    n = 0
    for r in rows:
        res = db[PRODUCTS].update_one(
            {"product_id": r["product_id"], "$or": [{"weight": {"$exists": False}}, {"weight": None}, {"weight": {"$lte": 0}}]},
            {"$set": {"weight": float(r["weight"])}},
        )
        if res.modified_count:
            db[BACKUPS].insert_one({**r, "kind": "copy", "run_id": run_id, "at": now})
            n += 1
    return n


def apply(db, rows: List[Dict[str, Any]], run_id: str) -> int:
    now = datetime.now(timezone.utc)
    done = 0
    for r in rows:
        twin = db[TWINS].find_one({"id": r["twin_id"]}, {"ecom": 1}) or {}
        ecom = twin.get("ecom") or {}
        marks = dirty_marks()
        db[BACKUPS].insert_one({
            **r, "run_id": run_id, "at": now,
            "prev_locally_modified": ecom.get("locally_modified"),
            "prev_edit_rev": ecom.get(EDIT_REV),
            "set_edit_rev": marks[EDIT_REV],
        })
        db[TWINS].update_one(
            {"id": r["twin_id"]}, {"$set": {f"ecom.{k}": v for k, v in marks.items()}}
        )
        done += 1
    return done


def revert(db, run_id: str) -> int:
    n = 0
    for b in db[BACKUPS].find({"run_id": run_id}):
        if b.get("kind") == "copy":
            res = db[PRODUCTS].update_one(
                {"product_id": b["product_id"], "weight": b["weight"]}, {"$unset": {"weight": ""}}
            )
            n += int(bool(res.modified_count))
            continue
        res = db[TWINS].update_one(
            {"id": b["twin_id"], f"ecom.{EDIT_REV}": b["set_edit_rev"]},
            {"$set": {
                "ecom.locally_modified": bool(b.get("prev_locally_modified")),
                f"ecom.{EDIT_REV}": b.get("prev_edit_rev"),
            }},
        )
        n += int(bool(res.modified_count))
    return n


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Queue live listings that have a weight. Dry run by default.")
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
        print(f"[REVERTED] run {args.run_id}: {revert(db, args.run_id)} listing(s) restored")
        return 0
    spines = list(db[PRODUCTS].find(
        {}, {"_id": 0, "product_id": 1, "pim_product_id": 1, "sku": 1, "weight": 1, "variant_of": 1}
    ))
    twins = list(db[TWINS].find(
        {}, {"_id": 0, "id": 1, "sku": 1, "weight": 1, "is_active": 1, "deleted_at": 1, "status": 1,
             "ecom.shopify_product_id": 1, "ecom.variant_of": 1, "ecom.status": 1, "ecom.taken_down_at": 1}
    ))
    copies = plan_copies(spines, twins)
    print(f"twins carrying a weight: {sum(1 for t in twins if _w(t.get('weight')) > 0)}")
    print(f"twin weights to copy onto a spine that has none: {len(copies)}")
    for c in copies:
        print(f"  {c['sku']}  {c['weight']} g")
    # The queue plan sees the spines as they will be after the copy.
    copied = {c["product_id"]: c["weight"] for c in copies}
    spines = [{**s, "weight": copied.get(s.get("product_id"), s.get("weight"))} for s in spines]
    p = plan(spines, twins)
    print(f"spines: {len(spines)}  with weight > 0: {p['weighted']}")
    print(f"twins: {len(twins)}  live listings: "
          f"{sum(1 for t in twins if (t.get('ecom') or {}).get('shopify_product_id'))}")
    print(f"weighted products not on a live, active listing (left alone): {len(p['not_live'])}")
    print(f"live listings to queue: {len(p['queue'])}")
    for r in p["queue"]:
        print(f"  {r['sku']}  <- {', '.join(r['weighted_skus'])}")
    if not args.apply:
        print("DRY RUN - nothing written. Re-run with --apply to copy and queue.")
        return 0
    run_id = uuid.uuid4().hex[:12]
    print(f"[APPLIED] run {run_id}: {apply_copies(db, copies, run_id)} twin weight(s) copied onto spines")
    print(f"[APPLIED] run {run_id}: {apply(db, p['queue'], run_id)} listing(s) queued "
          f"(undo: --revert --run-id {run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
