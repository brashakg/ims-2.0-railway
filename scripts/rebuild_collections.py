#!/usr/bin/env python3
"""
IMS 2.0 - Rebuild the website collections from IMS (owner 2026-10-19)
=====================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY: the 98 collections on bettervision.in were imported from an old app and
do not match what IMS sends. The owner chose to DELETE them all (no backup)
and let IMS keep one clean set it derives itself
(api/services/shopify_push/collection_registry.py).

DRY RUN (default): lists every Shopify collection that will be DELETED (all
that are not IMS's), every IMS menu item, Shopify menu item and home-page
theme setting that points at one of them (fix those first), and every IMS
collection that will be created / updated, with its handle and product count
(live now, and after the queued first publishes go out). Nothing is written.

--apply: 1) delete the old collections on Shopify (frees their handles),
2) mark the old ecom_collections rows retired (never pushed again) and record
that the rebuild has run (until then push-all and the scheduled sync leave
collections alone), 3) create / update the IMS set and publish each to the
Online Store. Stops before step 2 if any delete fails.

A RE-RUN deletes / retires only collections that existed at the first
rebuild: a collection staff made since is kept unless --include-new is
passed. --apply needs the same LIVE gates as the push.

USAGE
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/rebuild_collections.py
    ... scripts/rebuild_collections.py --apply
    ... scripts/rebuild_collections.py --apply --include-new   (re-run only)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))


def plan(db, shop):
    from api.services.shopify_push import collection_registry as reg

    names = reg.brand_names(db)
    live = {d["key"]: d["count"] for d in reg.derive_definitions(reg.listing_facts(db), names)}
    projected = reg.derive_definitions(reg.listing_facts(db, include_ready=True), names)
    return reg.collections_to_delete(db, shop), [dict(d, live=live.get(d["key"], 0)) for d in projected]


async def run(db, apply: bool, include_new: bool = False) -> dict:
    from api.services import shopify_push
    from api.services.shopify_push import collection_registry as reg

    live, reason = shopify_push._live_or_reason(db)
    if apply and not live:
        raise SystemExit(f"--apply refused: pushes are not LIVE ({reason}).")
    shop = await reg.shop_collections(db) if shopify_push._has_shopify_creds(db) else []
    if not shop and not live:
        print("[note] no Shopify credentials: the delete list below is empty for that reason.")
    doomed, defs = plan(db, shop)
    state = reg.rebuild_state(db)
    if state is not None and not include_new:
        first = set(state.get("old_ids") or [])
        for c in doomed:
            if c["id"] not in first:
                print(f"[kept] {c['handle']}: made after the first rebuild (pass --include-new to delete)")
        doomed = [c for c in doomed if c["id"] in first]

    print(f"\nDELETE on Shopify ({len(doomed)}):")
    for c in sorted(doomed, key=lambda c: c["handle"] or ""):
        print(f"  {c['handle'][:45]:45} {str(c['title'])[:40]:40} products={c['products']}")
    print(f"\nIMS collections ({len(defs)}; created only once 'live' > 0):")
    for d in defs:
        print(f"  {d['handle'][:30]:30} {d['title'][:36]:36} live={d['live']:<4} after publish={d['count']}")
    if not apply:
        refs = await reg.references_to(db, doomed) if shop else []
        print(f"\nPOINTS AT A COLLECTION TO BE DELETED ({len(refs)}; fix before --apply):")
        for r in refs:
            print(f"  {r}")
        print("\n[DRY-RUN] nothing written; re-run with --apply.")
        return {"delete": len(doomed), "collections": len(defs), "references": refs}

    for c in doomed:
        await reg.delete_shop_collection(db, c["id"])  # raises: nothing retired on a failure
        print(f"  deleted {c['handle']}")
    retired = reg.retire_old_collections(db) if state is None or include_new else 0
    if state is None:
        reg.mark_rebuilt(db, [c["id"] for c in doomed])
    first = reg.apply_collection_registry(db)
    linked = reg.link_shop_collections(db, shop)
    result = {**await reg.sync_collections(db), "created": first["created"], "linked": linked}
    print(f"\n[APPLY] deleted={len(doomed)} retired={retired} {result}")
    return {"deleted": len(doomed), "retired": retired, **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild the website collections from IMS. Dry run by default.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-new", action="store_true",
                        help="re-run only: also delete collections made after the first rebuild")
    args = parser.parse_args(argv)
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    asyncio.run(run(db, args.apply, args.include_new))
    return 0


if __name__ == "__main__":
    sys.exit(main())
