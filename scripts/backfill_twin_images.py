#!/usr/bin/env python3
"""
IMS 2.0 - Backfill product photos from the billing spine onto the catalog twin
==============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
The Shopify push reads the photograph off the catalog_products TWIN
(shopify_push.product_photo_urls: image_url / images[] / image), both for the
publish gate ("no photo, no publish") and for the media it attaches. The
create door's projection (product_master._build_pim_doc) never carried the
spine's `images` across, so on 2026-09-04 prod held 69 of 76 spine products
with an absolute photo URL and only 6 twins -- every IMS-catalogued product
was refused as "no photograph" while its photo sat one collection over.

The code fix (same PR) mirrors images at create and on edit. This script
repairs the rows that already exist: for every spine row with usable photos
whose twin has NONE, copy the spine's `images` onto the twin.

SCOPE
-----
  - reads `products` (the spine) and `catalog_products` (the twin), joined on
    products.pim_product_id -> catalog_products.id, with sku as the fallback
  - writes ONLY twins that carry no usable photo today; a twin that already
    has one is never touched (its photo may be the storefront's own copy)
  - does NOT queue the rows for the push by default: "cataloguing queues, a
    human presses publish" is about live edits; a repair that silently queued
    ~60 products would turn the next "push all pending" press into a bulk
    launch. Pass --queue to set ecom.locally_modified on the repaired twins
    when that IS the intent.
  - nothing external is written; no Shopify call

USAGE
-----
Dry-run (DEFAULT - prints the would-be repairs, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_twin_images.py

Apply:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_twin_images.py --apply
    ... add --queue to also mark the repaired twins for the manual push

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL (the vars `railway run` injects).
"""

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
os.environ.setdefault("JWT_SECRET_KEY", "backfill")
os.environ.setdefault("ENVIRONMENT", "script")

from api.services.shopify_push import product_photo_urls  # noqa: E402


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )


def plan(spines: List[Dict[str, Any]], twins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure: the repairs to make. Each = {twin_id, sku, images}. A twin with a
    usable photo already, or a spine with none, is not a repair."""
    by_id = {t.get("id"): t for t in twins if t.get("id")}
    by_sku = {t.get("sku"): t for t in twins if t.get("sku")}
    out: List[Dict[str, Any]] = []
    seen = set()
    for s in spines:
        photos = product_photo_urls(s)
        if not photos:
            continue
        twin = by_id.get(s.get("pim_product_id")) or by_sku.get(s.get("sku"))
        if twin is None or twin.get("id") in seen:
            continue
        if product_photo_urls(twin):
            continue
        seen.add(twin.get("id"))
        images = [u for u in (s.get("images") or []) if isinstance(u, str) and u.strip()]
        out.append({"twin_id": twin.get("id"), "sku": twin.get("sku"), "images": images})
    return out


def run(*, mongo_uri: Optional[str], db_name: str, apply: bool, queue: bool) -> Dict[str, Any]:
    if not mongo_uri:
        raise SystemExit(
            "No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass "
            "--mongo-uri, or run via `railway run` so the vars are injected."
        )
    from pymongo import MongoClient

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    spines = list(db["products"].find({}, {"_id": 0, "pim_product_id": 1, "sku": 1, "images": 1, "image_url": 1}))
    twins = list(db["catalog_products"].find({}, {"_id": 0, "id": 1, "sku": 1, "images": 1, "image_url": 1, "image": 1}))
    repairs = plan(spines, twins)

    print(f"spine rows: {len(spines)}  twins: {len(twins)}  twins to repair: {len(repairs)}")
    for r in repairs:
        print(f"  {r['sku'] or '-':32} twin={r['twin_id']} photos={len(r['images'])}")
    if not apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to write.")
        return {"repairs": len(repairs), "written": 0}

    written = 0
    for r in repairs:
        patch: Dict[str, Any] = {"images": r["images"]}
        if queue:
            patch["ecom.locally_modified"] = True
        res = db["catalog_products"].update_one({"id": r["twin_id"]}, {"$set": patch})
        written += int(res.modified_count)
    print(f"\nWROTE {written} twin(s)" + (" and queued them for the manual push" if queue else ""))
    return {"repairs": len(repairs), "written": written}


def main():
    parser = argparse.ArgumentParser(
        description="Copy spine product photos onto catalog twins that have none. Dry-run by default."
    )
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Also mark repaired twins ecom.locally_modified=True (they join the manual push queue).",
    )
    args = parser.parse_args()
    run(mongo_uri=resolve_mongo_uri(args.mongo_uri), db_name=args.db, apply=args.apply, queue=args.queue)


if __name__ == "__main__":
    sys.exit(main())
