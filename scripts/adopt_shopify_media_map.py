#!/usr/bin/env python3
"""
IMS 2.0 - Adopt a live product's Shopify media into ecom.media_map
==================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
PR #1128 made photos follow IMS onto Shopify, but ONLY for the media IMS
knows it owns: ``ecom.media_map = [{url: <IMS photo url>, id: <MediaImage
gid>}]`` on the catalog twin, written at attach. The 42 products that were
on Shopify before the map existed have none, so the photo pass keeps its
hands off them -- a replaced or removed photo in IMS never reaches the
storefront for those products until the map is adopted.

THE RULE (owner ticked "Adopt live products' photos")
-----------------------------------------------------
A claimed media can later be DELETED by the photo pass when IMS drops that
photo, so adoption claims a media ONLY on a POSITIVE identity match
(shopify_push.media.match_media_to_photos, the one rule the tests share):
the media's originalSource url IS the IMS url, or its alt IS the IMS url /
file name, or the two file names have the same stem. Never position, never
count. A product is adopted ONLY when EVERY IMS photo matched a distinct
media 1:1; a partial match is reported and nothing is written. Media no
photo claimed (hand uploads, connector media) is never touched -- it stays
unmanaged, exactly where it is.

Measured on prod 2026-09-06 (API 2024-10, 42 twins, 180 media): the stem
rule adopts the six 09-05 IMS-pushed products (15 media, 1:1, no unmanaged
left); the 36 Ray-Ban Meta products match under no safe rule.

SCOPE
-----
  - reads the twin (catalog_products) and, through the app's own transport,
    the product's media on Shopify -- a QUERY, never a mutation
  - writes ONLY ecom.media_map, through the ONE writer the photo pass uses
    (media._writeback_media_map); never locally_modified, never the photos
  - a twin that already carries a map is skipped (the attach wrote it)
  - one audit_logs row per adopted product (action MEDIA_MAP_ADOPT)
  - --ids is REQUIRED and explicit; 'all' is refused

USAGE
-----
Dry-run (DEFAULT - prints the would-be map per product, writes nothing):
    railway run --service ims-2.0-railway -- ".venv\\Scripts\\python.exe" scripts/adopt_shopify_media_map.py --ids <id>,<id>

Apply:
    ... scripts/adopt_shopify_media_map.py --ids <id>,<id> --apply

REVERSAL: $unset ecom.media_map on the printed ids --
    db.catalog_products.updateMany({id: {$in: [<ids>]}}, {$unset: {"ecom.media_map": ""}})
(the photo pass then goes back to hands-off on them; nothing on Shopify moves.)

Connection: MONGO_PUBLIC_URL, else MONGO_URL (the vars `railway run` injects);
Shopify creds resolve from the same injected env. Nothing secret is printed.
"""

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
os.environ.setdefault("JWT_SECRET_KEY", "adopt")
os.environ.setdefault("ENVIRONMENT", "script")

from agents.nexus_providers import _as_shopify_gid  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push.media import (  # noqa: E402
    _writeback_media_map,
    match_media_to_photos,
    owned_media,
    product_photo_urls,
)
from api.services.shopify_push.queries import _PRODUCT_MEDIA_QUERY  # noqa: E402

ACTOR = "system:adopt_shopify_media_map"
DB_NAME = "ims_2_0"


def parse_ids(raw: str) -> List[str]:
    """Explicit product ids only. 'all' (or an empty list) is refused: a
    claim is per product, printed and read by a human before --apply."""
    ids = [s.strip() for s in (raw or "").split(",") if s.strip()]
    if not ids or any(s.lower() == "all" for s in ids):
        raise SystemExit("--ids takes explicit comma-separated product ids; 'all' is refused.")
    return ids


async def inspect(db, product_id: str) -> Dict[str, Any]:
    """READ-ONLY: the twin, its IMS photos, its media on Shopify and the
    match. status: adopt | partial | unmatched | no_photos | already_mapped |
    missing | not_on_shopify | shopify_missing | graphql_error."""
    row: Dict[str, Any] = {
        "product_id": product_id,
        "sku": "-",
        "status": "missing",
        "photos": [],
        "media": 0,
        "map": [],
        "unmatched": [],
        "unmanaged": [],
    }
    twin = db["catalog_products"].find_one({"id": product_id})
    if twin is None:
        return row
    row["sku"] = twin.get("sku") or "-"
    gid = _as_shopify_gid((twin.get("ecom") or {}).get("shopify_product_id"), "Product")
    if not gid:
        row["status"] = "not_on_shopify"
        return row
    if owned_media(twin):
        row["status"] = "already_mapped"
        return row
    row["photos"] = product_photo_urls(twin)
    if not row["photos"]:
        row["status"] = "no_photos"
        return row
    body = await shopify_push._graphql(db, _PRODUCT_MEDIA_QUERY, {"id": gid})
    if body.get("errors"):
        row["status"] = "graphql_error"
        row["error"] = str(body["errors"])[:300]
        return row
    product = (body.get("data") or {}).get("product")
    if product is None:
        row["status"] = "shopify_missing"
        return row
    nodes = (product.get("media") or {}).get("nodes") or []
    match = match_media_to_photos(row["photos"], nodes)
    row["media"] = len(nodes)
    row["map"] = match["map"]
    row["unmatched"] = match["unmatched_photos"]
    row["unmanaged"] = match["unmanaged"]
    if match["map"] and not match["unmatched_photos"]:
        row["status"] = "adopt"
    elif match["map"]:
        row["status"] = "partial"
    else:
        row["status"] = "unmatched"
    return row


def _audit(db, row: Dict[str, Any]) -> None:
    """One MEDIA_MAP_ADOPT row per adopted product -- what was claimed, what
    was left unmanaged, how to reverse. Best-effort; the map write stands."""
    try:
        db["audit_logs"].insert_one(
            {
                "log_id": f"AUD-{uuid.uuid4().hex[:12]}",
                "action": "MEDIA_MAP_ADOPT",
                "entity_type": "catalog_product",
                "entity_id": row["product_id"],
                "user_id": ACTOR,
                "actor": ACTOR,
                "source": "ADOPT_SHOPIFY_MEDIA_MAP",
                "before_state": {"media_map": []},
                "after_state": {"media_map": row["map"], "unmanaged": row["unmanaged"]},
                "reversal": "update_one({'id': product_id}, {'$unset': {'ecom.media_map': ''}})",
                "severity": "INFO",
                "timestamp": datetime.now(tz=timezone.utc),
            }
        )
    except Exception as exc:  # noqa: BLE001 -- audit is best-effort
        print(f"      audit row skipped: {exc}")


async def run(db, ids: List[str], apply: bool) -> Dict[str, Any]:
    rows = [await inspect(db, pid) for pid in ids]
    for r in rows:
        print(
            f"  {r['sku']:32} {r['product_id']} {r['status']:14} photos={len(r['photos'])} "
            f"media={r['media']} matched={len(r['map'])} unmanaged={len(r['unmanaged'])}"
            + (f" error={r['error']}" if r.get("error") else "")
        )
        for m in r["map"]:
            print(f"      + {m['url']} -> {m['id']}")
        for u in r["unmatched"]:
            print(f"      ? unmatched {u}")
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})}
    totals = " ".join(f"{k}={v}" for k, v in counts.items())
    written: List[str] = []
    if apply:
        for r in rows:
            if r["status"] != "adopt":
                continue
            if _writeback_media_map(db, r["product_id"], r["map"]):
                _audit(db, r)
                written.append(r["product_id"])
            else:
                print(f"  WRITE FAILED {r['product_id']}")
        print(f"\nproducts={len(rows)} {totals} written={len(written)}")
        if written:
            print(
                "REVERSAL: db.catalog_products.updateMany({id: {$in: %s}}, "
                '{$unset: {"ecom.media_map": ""}})' % written
            )
    else:
        print(f"\nproducts={len(rows)} {totals} written=0")
        print("DRY RUN - nothing written. Re-run with --apply to write the maps marked 'adopt'.")
    return {"rows": rows, "written": written}


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adopt a live product's Shopify media into ecom.media_map. Dry-run by default."
    )
    parser.add_argument("--ids", required=True, help="Comma-separated catalog product ids ('all' refused).")
    parser.add_argument("--apply", action="store_true", help="Write the maps (default: dry-run).")
    return parser.parse_args(argv)


def _connect():
    """MONGO_PUBLIC_URL, else MONGO_URL; one retry on a server-selection
    timeout. The uri is never printed."""
    from pymongo import MongoClient
    from pymongo.errors import ServerSelectionTimeoutError

    uri = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGO_URL")
    if not uri:
        raise SystemExit("No Mongo connection: set MONGO_PUBLIC_URL / MONGO_URL or run via `railway run`.")
    for attempt in (1, 2):
        client = MongoClient(uri, serverSelectionTimeoutMS=20000)
        try:
            client.admin.command("ping")
            return client[DB_NAME]
        except ServerSelectionTimeoutError:
            if attempt == 2:
                raise
            print("Mongo server selection timed out; retrying once")
    return None


def main(argv=None):
    args = parse_args(argv)
    ids = parse_ids(args.ids)
    db = _connect()
    asyncio.run(run(db, ids, apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
