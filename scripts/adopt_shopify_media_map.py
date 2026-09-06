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
  R1  the media's originalSource url IS the IMS url, or
  R3  the media's CDN file name IS the IMS url's file name -- Shopify's own
      ``_<uuid>`` collision suffix is ignored on the CDN side only, the
      extension must agree exactly whenever the IMS name has one, nothing
      else is normalised (no ``_WxH``, no case folding of stem OR extension,
      no bare-hex strip). An extension-less IMS name of ANY shape (not only
      the uploader's ObjectId) matches any image extension: 'front' claims
      'front.png' -- wider than the ObjectId case, none such on the 42.
Never position, never count, and NO alt rule (dropped 09-06 on the
verifier's finding: IMS attaches every photo with alt '', so an alt equal
to an IMS url / file name can only be a human's edit on a media IMS did not
attach -- it fired 0 times on the 42 and could only ever claim wrongly).
A product is adopted ONLY when EVERY IMS photo matched a distinct media 1:1;
a partial match is reported and nothing is written. Media no photo claimed
(hand uploads, connector media) is never touched -- it stays unmanaged,
exactly where it is, and the dry-run prints its file name so a wrong pairing
is visible BEFORE --apply.

KNOWN LIMIT (inherent to a file-name rule): a HUMAN'S upload that carries the
SAME file name as an IMS photo IS claimed -- IMS 'x/1.jpg' claims a hand
upload named '1.jpg', and IMS 'front.jpg' claims 'front_<uuid>.jpg' (a hand
upload that collided with some other front.jpg in Files). The rule cannot
tell them apart; the only defence is the printed pair. READ every
'+ <ims url> -> <gid> (<cdn file name>)' line in the dry-run and drop the id
from --ids when a name is not one IMS uploaded (0 such pairs on the 42:
the six adopt candidates carry 24-hex ObjectId names and no hand uploads).

Measured on prod 2026-09-06 (API 2024-10, 42 twins, 180 media): R3 adopts
the six 09-05 IMS-pushed products (15 media, ObjectId <-> ObjectId.png/jpg/
webp, 1:1, no unmanaged left) -- ROUND 1, applied 09-06; R1 fires on
nothing (originalSource is always Shopify's own storage copy). The 42 hold
0 same-base-name/different-uuid pairs, 0 non-ASCII and 0 upper-case CDN
names.

ROUND 2 (owner rulings 2026-09-06) -- two OPT-IN doors, each REQUIRES --ids
---------------------------------------------------------------------------
(1) ``--rule connector-prefix``: adds R3b for the 23 Ray-Ban Meta products
    the connector created. A Shopify media whose CDN file name is
    '<this product's OWN Shopify numeric id>__<nn>__<basename>' matches the
    IMS photo whose file name equals <basename> stem-for-stem, extension
    IGNORED (the connector re-encoded 4_1.jpeg as 4_1.png). The prefix MUST
    be the product's own id (a foreign id, or no prefix, never matches);
    stems are exact (no case folding, no uuid strip). Still 1:1, still
    partial = report, and the other 3-5 connector media on each product
    (different basenames) stay unmanaged. R1/R3 stay on underneath.
(2) ``--replace-photos-from-shopify``: for the 6 old-app twins
    (ecom.source=bvi_import) that hold 2-3 STALE cdn.shopify.com screenshot
    links as IMS photos while Shopify holds ONE connector image. Their IMS
    photo list is REPLACED with that one Shopify image (the media's CDN url)
    and the map adopted as {url: that cdn url, id: media gid}. Refused,
    BEFORE the Shopify query and before any write, when the twin is not an
    old-app twin (ecom.source != bvi_import), when any of its photos is not
    a cdn.shopify.com link (a real in-app photo is never overwritten here --
    prod aa7d0ed2 has exactly the one-media shape a pasted-wrong id would
    hand over), when a map already exists, when the twin carries a singular
    image_url/image the door cannot clear, when its spine row lacks a
    product_id (the door cannot key it; falling through to a twin-only
    write is exactly what the next spine edit would undo), or when the spine's
    pim_product_id names ANOTHER twin (the mirror keys the twin on that
    field first, so the door would move a foreign product's photo); and,
    after the query, when the Shopify media count != 1. Nothing changes on
    the storefront: Shopify already shows that image.

    HOW THE PHOTO IS WRITTEN. Photos live on the billing SPINE
    (products.images[]) and are mirrored to the catalog twin by the ONE
    mirror rule (product_master.mirror_update_to_catalog_twin); a twin-only
    write would be overwritten by the next spine edit. So the replace goes
    through the SAME service door the product photo edit uses
    (product_master.update_product -> repo.update + the mirror) with
    ``mark_dirty=False`` (the twin follows, nothing queues). A twin with NO
    spine row (memory: one of the six was archived rather than promoted)
    is written through the same mirror helper with an empty spine -- never
    a second twin writer. After the write the twin is re-read and the map
    is adopted ONLY when product_photo_urls(twin) is exactly [that cdn url].

    REVERSAL. Every PLANNED product's previous photo list (spine images,
    twin photo list + raw twin images[], previous map) is saved to
    ``adopt_replace_reversal_<UTC stamp>.json`` under --reversal-dir
    (default: the working directory; pass a directory OUTSIDE the git
    checkout under the prod recipe) BEFORE the first write, and printed per
    product; the file is rewritten after the loop with ``applied`` = the ids
    that moved. So an un-caught failure between two products, or a mirror
    that swallowed a twin error (fail-soft) after the spine moved -- printed
    as 'TWIN DID NOT FOLLOW', the map NOT adopted -- still leaves the
    before-state on disk. To reverse: put the saved ``spine_images`` back
    through the same door (PUT /products/{spine_id} images=[...]; a null
    means the spine had no images key) and $unset ecom.media_map; a twin
    without a spine takes its ``twin_images`` back on
    catalog_products.images directly.

SCOPE
-----
  - reads the twin (catalog_products) and, through the app's own transport,
    the product's media on Shopify -- a QUERY, never a mutation
  - writes ONLY ecom.media_map, through the ONE writer the photo pass uses
    (media._writeback_media_map); never locally_modified. The replace mode
    ALSO writes the photo list, through the product edit door (above).
  - a twin that already carries a map is skipped (the attach wrote it); a
    map that is [] or holds only malformed rows counts as NO map (owned_media
    drops such rows -- the pass pruned it or never wrote it) and is adopted
  - one audit_logs row per adopted product (action MEDIA_MAP_ADOPT; the
    replace mode writes PHOTOS_REPLACED_FROM_SHOPIFY carrying the before list)
  - --ids is REQUIRED and explicit; 'all' is refused

ENVIRONMENT NOTES
-----------------
  - the map's ``url`` is whatever product_photo_urls(twin) yields IN THE
    ENV THIS SCRIPT RUNS IN, and the photo pass later DELETES an owned row
    whose url is not in its own photo list (then attaches a duplicate). So
    run it under `railway run` against the SAME service as the pass
    (PUBLIC_API_BASE_URL is unset on ims-2.0-railway today and the 42 store
    ABSOLUTE up.railway.app image urls, so the two cannot disagree; if that
    variable is ever set, both envs still see it through `railway run`).
  - _writeback_media_map is a whole-``ecom`` read-merge-write, the same
    idiom as the other ecom writers, so a write-back from the scheduled
    sync (01:00 / 09:00 IST) running at the same instant can clobber the
    map or be clobbered -- benign (a lost map = hands-off again; re-run),
    but run --apply OUTSIDE those windows.

USAGE
-----
Dry-run (DEFAULT - prints the would-be map per product, writes nothing):
    railway run --service ims-2.0-railway -- ".venv\\Scripts\\python.exe" scripts/adopt_shopify_media_map.py --ids <id>,<id>

Apply:
    ... scripts/adopt_shopify_media_map.py --ids <id>,<id> --apply
Round 2:
    ... --ids <id>,<id> --rule connector-prefix [--apply]
    ... --ids <id>,<id> --replace-photos-from-shopify [--reversal-dir <dir>] [--apply]

REVERSAL (map only): $unset ecom.media_map on the printed ids --
    db.catalog_products.updateMany({id: {$in: [<ids>]}}, {$unset: {"ecom.media_map": ""}})
(the photo pass then goes back to hands-off on them; nothing on Shopify moves.)

Connection: MONGO_PUBLIC_URL, else MONGO_URL (the vars `railway run` injects);
Shopify creds resolve from the same injected env. Nothing secret is printed.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
os.environ.setdefault("JWT_SECRET_KEY", "adopt")
os.environ.setdefault("ENVIRONMENT", "script")

from agents.nexus_providers import _as_shopify_gid  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push.media import (  # noqa: E402
    _file_name,
    _writeback_media_map,
    match_media_to_photos,
    owned_media,
    product_photo_urls,
)
from api.services.shopify_push.queries import _PRODUCT_MEDIA_QUERY  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402

ACTOR = "system:adopt_shopify_media_map"
DB_NAME = "ims_2_0"
_URL = re.compile(r"https?://\S+|\S*myshopify\.com\S*")
RULES = {"exact": ("exact",), "connector-prefix": ("exact", "connector_prefix")}


class _Conn:
    """What the mirror expects of a connection (get_collection + a truthy
    is_connected) over a raw pymongo Database -- whose own attribute lookup
    would hand back a Collection named 'is_connected' instead."""

    is_connected = True

    def __init__(self, db):
        self._db = db

    def get_collection(self, name):
        return self._db[name]


def _redact(exc: BaseException) -> str:
    """A transport failure's message with any url blanked: the shop url is an
    injected env value and this output is pasted into reports."""
    return f"{type(exc).__name__}: {_URL.sub('<url>', str(exc))[:200]}"


def parse_ids(raw: str) -> List[str]:
    """Explicit product ids only. 'all' (or an empty list) is refused: a
    claim is per product, printed and read by a human before --apply."""
    ids = [s.strip() for s in (raw or "").split(",") if s.strip()]
    if not ids or any(s.lower() == "all" for s in ids):
        raise SystemExit("--ids takes explicit comma-separated product ids; 'all' is refused.")
    return ids


def _row(product_id: str) -> Dict[str, Any]:
    return {
        "product_id": product_id,
        "sku": "-",
        "status": "missing",
        "photos": [],
        "media": 0,
        "map": [],
        "unmatched": [],
        "unmanaged": [],
        "names": {},
        "before_map": None,
    }


async def _media_nodes(db, gid: str, row: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """The product's media nodes on Shopify -- a QUERY. None (with row.status
    set) on a transport failure, a GraphQL error body or a missing product."""
    try:
        body = await shopify_push._graphql(db, _PRODUCT_MEDIA_QUERY, {"id": gid})
    except Exception as exc:  # noqa: BLE001 -- retries spent / non-retryable 4xx / connect error
        row["status"] = "graphql_error"
        row["error"] = _redact(exc)
        return None
    if body.get("errors"):
        row["status"] = "graphql_error"
        row["error"] = str(body["errors"])[:300]
        return None
    product = (body.get("data") or {}).get("product")
    if product is None:
        row["status"] = "shopify_missing"
        return None
    return (product.get("media") or {}).get("nodes") or []


async def inspect(db, product_id: str, rules: tuple = ("exact",)) -> Dict[str, Any]:
    """READ-ONLY: the twin, its IMS photos, its media on Shopify and the
    match. status: adopt | partial | unmatched | no_photos | already_mapped |
    missing | not_on_shopify | shopify_missing | graphql_error (a GraphQL
    error body OR the transport giving up -- reported, never fatal, so the
    other ids in the run still get their report). already_mapped means
    owned_media(twin) is non-empty; an empty or all-malformed map is not."""
    row = _row(product_id)
    twin = db["catalog_products"].find_one({"id": product_id})
    if twin is None:
        return row
    row["sku"] = twin.get("sku") or "-"
    row["before_map"] = (twin.get("ecom") or {}).get("media_map")
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
    nodes = await _media_nodes(db, gid, row)
    if nodes is None:
        return row
    match = match_media_to_photos(row["photos"], nodes, rules=rules, product_gid=gid)
    row["media"] = len(nodes)
    row["map"] = match["map"]
    row["unmatched"] = match["unmatched_photos"]
    row["unmanaged"] = match["unmanaged"]
    row["names"] = match["names"]
    if match["map"] and not match["unmatched_photos"]:
        row["status"] = "adopt"
    elif match["map"]:
        row["status"] = "partial"
    else:
        row["status"] = "unmatched"
    return row


def _spine_of(db, twin_id: str) -> Optional[Dict[str, Any]]:
    """The billing spine row behind a twin: keyed on its own id (the promote
    door: product_id == twin id) or on pim_product_id (the create door)."""
    coll = db["products"]
    return coll.find_one({"product_id": twin_id}) or coll.find_one({"pim_product_id": twin_id})


async def inspect_replace(db, product_id: str) -> Dict[str, Any]:
    """READ-ONLY plan for --replace-photos-from-shopify. status: replace |
    already_mapped | twin_has_image_url | not_bvi_import | photos_not_cdn |
    spine_unkeyed | spine_points_elsewhere | not_one_media | no_cdn_url |
    missing | not_on_shopify | shopify_missing | graphql_error -- every
    refusal but the media ones is decided BEFORE the Shopify query, and all
    of them before any write. ``photos`` is the twin's CURRENT list (the
    before), ``map`` the one row to adopt."""
    row = _row(product_id)
    row.update({"spine_id": None, "spine_images": None, "twin_images": None})
    twin = db["catalog_products"].find_one({"id": product_id})
    if twin is None:
        return row
    ecom = twin.get("ecom") or {}
    row["sku"] = twin.get("sku") or "-"
    row["before_map"] = ecom.get("media_map")
    row["photos"] = product_photo_urls(twin)
    row["twin_images"] = twin.get("images")
    gid = _as_shopify_gid(ecom.get("shopify_product_id"), "Product")
    if not gid:
        row["status"] = "not_on_shopify"
        return row
    if owned_media(twin):
        row["status"] = "already_mapped"
        return row
    # The door writes images[] only; a singular image_url/image would stay in
    # product_photo_urls and the list would not be the one photo.
    if any(twin.get(k) for k in ("image_url", "image")):
        row["status"] = "twin_has_image_url"
        return row
    # SCOPE (the ruling): an old-app twin whose photos are ALL stale
    # cdn.shopify.com links. A real in-app photo is never overwritten here.
    if ecom.get("source") != "bvi_import":
        row["status"] = "not_bvi_import"
        return row
    if not row["photos"] or any(urlsplit(u).netloc != "cdn.shopify.com" for u in row["photos"]):
        row["status"] = "photos_not_cdn"
        return row
    # The spine the door writes MUST mirror to THIS twin (the mirror keys the
    # twin on spine.pim_product_id first): a spine pointing elsewhere would
    # move a foreign product's photo; a row without product_id cannot go
    # through the door at all and would fall through to a twin-only write.
    spine = _spine_of(db, product_id)
    if spine is not None:
        if not spine.get("product_id"):
            row["status"] = "spine_unkeyed"
            return row
        if spine.get("pim_product_id") and spine["pim_product_id"] != product_id:
            row["status"] = "spine_points_elsewhere"
            return row
        row["spine_id"] = spine["product_id"]
        row["spine_images"] = spine.get("images")
    nodes = await _media_nodes(db, gid, row)
    if nodes is None:
        return row
    row["media"] = len(nodes)
    row["names"] = {
        str(n.get("id")): _file_name((n.get("image") or {}).get("url") or "")
        for n in nodes
        if isinstance(n, dict)
    }
    if len(nodes) != 1 or not isinstance(nodes[0], dict) or not nodes[0].get("id"):
        row["status"] = "not_one_media"
        return row
    cdn = str((nodes[0].get("image") or {}).get("url") or "").strip()
    if not cdn:
        row["status"] = "no_cdn_url"
        return row
    row["map"] = [{"url": cdn, "id": str(nodes[0]["id"])}]
    row["status"] = "replace"
    return row


def _replace(db, row: Dict[str, Any]) -> bool:
    """Write the one Shopify image as the product's photo list through the
    product edit door (spine -> mirror -> twin, nothing queued), verify the
    twin now shows exactly that photo, then adopt the map. Never raises:
    every failure is reported per product, and this product's reversal
    entry is already on disk (run() saves the plan before the first write)."""
    pid = row["product_id"]
    cdn = row["map"][0]["url"]
    try:
        if row["spine_id"]:
            pm.update_product(
                product_id=row["spine_id"],
                patch={"images": [cdn]},
                actor=ACTOR,
                product_repo=ProductRepository(db["products"]),
                db=_Conn(db),
                mark_dirty=False,
            )
        else:
            pm.mirror_update_to_catalog_twin(
                product_id=pid, current={}, patch={"images": [cdn]}, db=_Conn(db), mark_dirty=False
            )
        now = product_photo_urls(db["catalog_products"].find_one({"id": pid}) or {})
        if now != [cdn]:
            # ponytail: the mirror is fail-soft, so a swallowed twin error
            # leaves the spine (when there is one) already at [cdn] -- said
            # so here, the before-list is in the reversal file. Upgrade: put
            # spine_images back through the same door right here.
            print(
                f"  TWIN DID NOT FOLLOW {pid}: twin holds {now}, spine "
                f"{'written to [' + cdn + ']' if row['spine_id'] else 'none'} -- map not adopted; "
                "restore spine_images from the reversal file"
            )
            return False
        if not _writeback_media_map(db, pid, row["map"]):
            print(f"  WRITE FAILED {pid}: media_map -- photos moved; restore from the reversal file")
            return False
        return True
    except Exception as exc:  # noqa: BLE001 -- reported per product, the run goes on
        print(f"  WRITE FAILED {pid}: {_redact(exc)} -- see the reversal file")
        return False


def _save_reversal(path: str, plan: List[Dict[str, Any]], applied: List[str]) -> None:
    """The before-state of every PLANNED product. Written BEFORE the first
    write (applied=[]) and again after the loop (applied=the ids that moved),
    so a crash between two products never loses the first one's lists."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "how": "put spine_images back through PUT /products/{spine_id} (or twin_images on "
                "catalog_products.images when spine_id is null) and $unset ecom.media_map",
                "applied": list(applied),
                "products": [
                    {
                        "product_id": r["product_id"],
                        "spine_id": r["spine_id"],
                        "spine_images": r["spine_images"],
                        "twin_photos": r["photos"],
                        "twin_images": r["twin_images"],
                        "media_map_before": r["before_map"],
                        "photo_after": r["map"][0]["url"],
                        "media_map_after": r["map"],
                    }
                    for r in plan
                ],
            },
            fh,
            indent=1,
        )


def _audit(db, row: Dict[str, Any], *, action: str, before: Dict[str, Any], after: Dict[str, Any], reversal: str) -> None:
    """One audit row per adopted product -- what was claimed, what was left
    unmanaged, how to reverse. Best-effort; the map write stands."""
    try:
        db["audit_logs"].insert_one(
            {
                "log_id": f"AUD-{uuid.uuid4().hex[:12]}",
                "action": action,
                "entity_type": "catalog_product",
                "entity_id": row["product_id"],
                "user_id": ACTOR,
                "actor": ACTOR,
                "source": "ADOPT_SHOPIFY_MEDIA_MAP",
                "before_state": before,
                "after_state": after,
                "reversal": reversal,
                "severity": "INFO",
                "timestamp": datetime.now(tz=timezone.utc),
            }
        )
    except Exception as exc:  # noqa: BLE001 -- audit is best-effort
        print(f"      audit row skipped: {exc}")


def _print_row(r: Dict[str, Any]) -> None:
    print(
        f"  {r['sku']:32} {r['product_id']} {r['status']:18} photos={len(r['photos'])} "
        f"media={r['media']} matched={len(r['map'])} unmanaged={len(r['unmanaged'])}"
        + (f" spine={r['spine_id'] or 'none'}" if "spine_id" in r else "")
        + (f" error={r['error']}" if r.get("error") else "")
    )
    names = r["names"]
    if "spine_id" in r:
        for u in r["photos"]:
            print(f"      before {u}")
        for m in r["map"]:
            print(f"      after  {m['url']} -> {m['id']} ({names.get(m['id'], '?')})")
        if r["status"] == "not_one_media":
            for mid, name in names.items():
                print(f"      - media {mid} ({name})")
        return
    for m in r["map"]:
        print(f"      + {m['url']} -> {m['id']} ({names.get(m['id'], '?')})")
    for u in r["unmatched"]:
        print(f"      ? unmatched {u}")
    for mid in r["unmanaged"]:
        print(f"      - unmanaged {mid} ({names.get(mid, '?')}) -- left where it is")


async def run(
    db,
    ids: List[str],
    apply: bool,
    *,
    rules: tuple = ("exact",),
    replace: bool = False,
    reversal_dir: str = ".",
) -> Dict[str, Any]:
    if replace:
        rows = [await inspect_replace(db, pid) for pid in ids]
    else:
        rows = [await inspect(db, pid, rules) for pid in ids]
    for r in rows:
        _print_row(r)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})}
    totals = " ".join(f"{k}={v}" for k, v in counts.items())
    written: List[str] = []
    reversal_path: Optional[str] = None
    if apply and replace:
        plan = [r for r in rows if r["status"] == "replace"]
        if plan:
            os.makedirs(reversal_dir, exist_ok=True)
            reversal_path = os.path.abspath(
                os.path.join(
                    reversal_dir,
                    f"adopt_replace_reversal_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
                )
            )
            _save_reversal(reversal_path, plan, applied=[])  # BEFORE the first write
        for r in plan:
            if _replace(db, r):
                _audit(
                    db,
                    r,
                    action="PHOTOS_REPLACED_FROM_SHOPIFY",
                    before={"photos": r["photos"], "spine_images": r["spine_images"], "media_map": r["before_map"]},
                    after={"photos": [r["map"][0]["url"]], "media_map": r["map"]},
                    reversal="restore spine_images via PUT /products/{spine_id} images=[...]; $unset ecom.media_map",
                )
                written.append(r["product_id"])
                print(f"      previous photos {r['product_id']}: {r['photos']}")
        if plan:
            _save_reversal(reversal_path, plan, applied=written)
        print(f"\nproducts={len(rows)} {totals} written={len(written)}")
        if reversal_path:
            print(f"REVERSAL saved to {reversal_path} (the plan, written before the first write; 'applied' = what moved)")
        if written:
            print(
                "REVERSAL (map only): db.catalog_products.updateMany({id: {$in: %s}}, "
                '{$unset: {"ecom.media_map": ""}})' % written
            )
    elif apply:
        for r in rows:
            if r["status"] != "adopt":
                continue
            if _writeback_media_map(db, r["product_id"], r["map"]):
                _audit(
                    db,
                    r,
                    action="MEDIA_MAP_ADOPT",
                    before={"media_map": r["before_map"]},
                    after={"media_map": r["map"], "unmanaged": r["unmanaged"]},
                    reversal="update_one({'id': product_id}, {'$unset': {'ecom.media_map': ''}})",
                )
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
        want = "replace" if replace else "adopt"
        print(f"DRY RUN - nothing written. Re-run with --apply to write the products marked '{want}'.")
    return {"rows": rows, "written": written, "reversal_path": reversal_path}


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adopt a live product's Shopify media into ecom.media_map. Dry-run by default."
    )
    parser.add_argument("--ids", required=True, help="Comma-separated catalog product ids ('all' refused).")
    parser.add_argument("--apply", action="store_true", help="Write the maps (default: dry-run).")
    parser.add_argument(
        "--rule",
        choices=sorted(RULES),
        default="exact",
        help="exact (R1/R3, default) or connector-prefix (adds R3b: '<own id>__<nn>__<basename>', extension ignored).",
    )
    parser.add_argument(
        "--replace-photos-from-shopify",
        action="store_true",
        help="Replace the IMS photo list with the product's ONE Shopify image and adopt it (refused when media count != 1).",
    )
    parser.add_argument(
        "--reversal-dir",
        default=".",
        help="Where --replace-photos-from-shopify --apply saves adopt_replace_reversal_<UTC>.json (default: cwd; "
        "under the prod recipe pass a directory outside the git checkout).",
    )
    args = parser.parse_args(argv)
    if args.replace_photos_from_shopify and args.rule != "exact":
        parser.error("--replace-photos-from-shopify takes no --rule: the single Shopify image is adopted as-is.")
    return args


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
    # a non-ASCII CDN file name must not crash the report under cp1252
    sys.stdout.reconfigure(errors="backslashreplace")
    db = _connect()
    asyncio.run(
        run(
            db,
            ids,
            apply=args.apply,
            rules=RULES[args.rule],
            replace=args.replace_photos_from_shopify,
            reversal_dir=args.reversal_dir,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
