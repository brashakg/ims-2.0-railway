#!/usr/bin/env python3
"""
IMS 2.0 -- Hard-delete catalogue products (and everything hanging off them)
===========================================================================

WHY THIS EXISTS
---------------
The app's Delete button (`DELETE /api/v1/catalog/products/{id}`) is a SOFT
delete: it sets `is_active = False` on the catalog twin and on the billing
spine and keeps both rows. The `identity_key` unique index on `products` is
NOT filtered by `is_active` (ProductRepository.find_by_identity_key does a
plain find_one), so a soft-deleted product STILL OWNS its brand+model+colour
+size identity -- and re-adding the same item through Add-a-Product 409s.

That is the trap the 2026-09-07 catalogue reset had to work around. This
script is the recorded, repeatable version of that reset, scoped to a chosen
set of products instead of the whole catalogue.

USE IT WHEN: a product must go away so the SAME item can be catalogued again.
DO NOT USE IT for "take this off sale" -- that is the Delete button's job, and
it also takes the listing off the website. A hard delete does neither.

WHAT IT REMOVES  (per targeted product)
---------------------------------------
  catalog_products     the twin (keyed on `id`, or `sku`)
  products             the billing spine (`product_id` == twin id, or
                       `pim_product_id` == twin id, or the shared `sku`)
  catalog_variants     rows whose parent_product_id / parent_sku / sku match
  product_images       rows whose product_id matches either id
  collection_products  rows whose product_id / sku match
  stock_units          rows whose product_id matches either id

REFUSALS  (checked for EVERY target BEFORE anything is written; any hit
aborts the whole run, so a partial delete is impossible)
---------------------------------------------------------------------------
  * The product is live or pushed on Shopify (`ecom.shopify_product_id` set).
    Take it off Shopify first -- this script does not call Shopify.
  * The product appears on any order line (`orders.items.product_id`).
    Deleting it would gut an invoice. There is no override.
  * The product has a stock unit that is not AVAILABLE (SOLD / RESERVED /
    anything else). That unit is money or a promise; sort it out first.

SAFETY CONTRACT
---------------
- --dry-run is the DEFAULT. NOTHING is written without --commit.
- A JSON snapshot of every document about to be deleted is written BEFORE the
  first delete, and --commit aborts if the snapshot cannot be written.
- --all requires --expect N and aborts unless exactly N products are targeted.
- One `audit_logs` row records the run (action `catalog.product.hard_deleted`).
- Reads MONGODB_URL (preferred) or MONGO_URL from env.
- pymongo lazy-imported. No emojis -- Windows cp1252 safe.

Usage
-----
  # Dry run (default) -- shows the blast radius, writes nothing:
  python scripts/delete_catalog_products.py --all --expect 4

  # Same, for named products:
  python scripts/delete_catalog_products.py --sku FRM-1001,FRM-1002
  python scripts/delete_catalog_products.py --id prod_a1b2c3d4e5f6

  # Delete for real (MUTATES PROD -- owner-gated, run via `railway run`):
  python scripts/delete_catalog_products.py --all --expect 4 --commit

Undo: the snapshot file is a plain JSON object of
{collection: [documents]} -- re-inserting it restores exactly what was
removed. Nothing else in the database is touched.

Exit codes: 0 = OK / dry-run done;  1 = refused or fatal error.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("delete_catalog_products")

# stock_units statuses that mean "on the shelf, nobody has claimed it".
# Mirrors ProductRepository.AVAILABLE_STATUS_VALUES -- legacy rows carry the
# lowercase forms, and inventory already treats those as on-hand.
AVAILABLE_STATUS_VALUES = ("AVAILABLE", "available", "Available")

AUDIT_ACTION = "catalog.product.hard_deleted"


# ---------------------------------------------------------------------------
# Pure helpers (no DB dependency -- unit-testable)
# ---------------------------------------------------------------------------

def parse_csv_option(raw: Optional[str]) -> List[str]:
    """Split a comma-separated CLI value into trimmed, non-empty parts."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def product_label(twin: Dict[str, Any], spine: Optional[Dict[str, Any]]) -> str:
    """A human-readable one-liner for a product, for the console and the
    snapshot. Falls back through twin then spine then the raw id, because a
    door-created twin and its spine do not always carry the same fields."""
    source = twin or {}
    fallback = spine or {}
    brand = source.get("brand") or fallback.get("brand") or ""
    model = source.get("model") or fallback.get("model") or ""
    name = source.get("name") or fallback.get("name") or ""
    sku = source.get("sku") or fallback.get("sku") or ""
    title = " ".join(part for part in (str(brand), str(model)) if part).strip()
    if not title:
        title = str(name).strip()
    if not title:
        title = str(source.get("id") or fallback.get("product_id") or "unknown")
    return f"{title} (SKU {sku or 'none'})"


def shopify_id_of(twin: Dict[str, Any]) -> Optional[str]:
    """The Shopify product gid stamped on a twin, or None. `ecom` is absent on
    a product that has never been pushed, which is the normal case for a
    freshly catalogued item -- first publish is always a human press."""
    ecom = (twin or {}).get("ecom") or {}
    if not isinstance(ecom, dict):
        return None
    gid = ecom.get("shopify_product_id")
    return str(gid) if gid else None


def unavailable_units(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stock units that are NOT plain available stock -- sold, reserved, or any
    status this script does not recognise. Erring towards refusal is the point:
    an unknown status is never assumed to be safe to delete."""
    return [
        unit
        for unit in units
        if str(unit.get("status") or "") not in AVAILABLE_STATUS_VALUES
    ]


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def connect() -> Optional[Any]:
    """Return a pymongo Database or None (fail-soft, same contract as
    scripts/prod_data_cleanup.py)."""
    mongo_url = os.environ.get("MONGODB_URL") or os.environ.get("MONGO_URL")
    if not mongo_url:
        logger.error("MONGODB_URL / MONGO_URL not set -- nothing to connect to.")
        return None

    try:
        from pymongo import MongoClient  # lazy import  # noqa: PLC0415

        client = MongoClient(mongo_url, serverSelectionTimeoutMS=8000)
        client.admin.command("ping")
        db_name = os.environ.get("MONGO_DATABASE", "ims_2_0")
        logger.info("Connected to MongoDB database: %s", db_name)
        return client[db_name]
    except ImportError:
        logger.error("pymongo is not installed -- cannot run.")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("MongoDB connection failed: %s", exc)
        return None


def find_twins(db: Any, *, want_all: bool, ids: List[str], skus: List[str]) -> List[Dict]:
    """The catalog_products docs this run targets.

    --all takes the whole catalogue; otherwise ids and skus are OR-ed, so a
    mixed list works. Returns the docs as stored (with `_id`) because the
    snapshot must round-trip."""
    coll = db["catalog_products"]
    if want_all:
        return list(coll.find({}))

    clauses: List[Dict[str, Any]] = []
    if ids:
        clauses.append({"id": {"$in": ids}})
        # A twin created before the convergence can be keyed on product_id.
        clauses.append({"product_id": {"$in": ids}})
    if skus:
        clauses.append({"sku": {"$in": skus}})
    if not clauses:
        return []
    return list(coll.find({"$or": clauses}))


def resolve_spine(db: Any, twin: Dict[str, Any]) -> Optional[Dict]:
    """The billing spine row for a twin.

    Mirrors catalog._spine_product_id, plus the pim_product_id arm: a
    door-created spine keeps its OWN uuid and points at the twin through
    `pim_product_id`, so the shared-id lookup misses for most live products."""
    coll = db["products"]
    twin_id = twin.get("id") or twin.get("product_id")
    if twin_id:
        found = coll.find_one({"product_id": twin_id})
        if found:
            return found
        found = coll.find_one({"pim_product_id": twin_id})
        if found:
            return found
    sku = twin.get("sku")
    if sku:
        return coll.find_one({"sku": sku})
    return None


def collect_target(db: Any, twin: Dict[str, Any]) -> Dict[str, Any]:
    """Everything that hangs off one product, gathered but not yet deleted.

    Both ids are carried through every lookup: a door-created product has a
    twin id AND a different spine product_id, and the child collections were
    written by different doors over the years against one or the other."""
    spine = resolve_spine(db, twin)
    sku = twin.get("sku") or (spine or {}).get("sku")

    ids: Set[str] = set()
    for candidate in (twin.get("id"), twin.get("product_id"), (spine or {}).get("product_id")):
        if candidate:
            ids.add(str(candidate))
    id_list = sorted(ids)

    variant_clauses: List[Dict[str, Any]] = []
    if id_list:
        variant_clauses.append({"parent_product_id": {"$in": id_list}})
    if sku:
        variant_clauses.append({"parent_sku": sku})
        variant_clauses.append({"sku": sku})
    variants = list(db["catalog_variants"].find({"$or": variant_clauses})) if variant_clauses else []

    images = list(db["product_images"].find({"product_id": {"$in": id_list}})) if id_list else []

    collection_clauses: List[Dict[str, Any]] = []
    if id_list:
        collection_clauses.append({"product_id": {"$in": id_list}})
    if sku:
        collection_clauses.append({"sku": sku})
    collection_rows = (
        list(db["collection_products"].find({"$or": collection_clauses}))
        if collection_clauses
        else []
    )

    units = list(db["stock_units"].find({"product_id": {"$in": id_list}})) if id_list else []

    order_count = (
        db["orders"].count_documents({"items.product_id": {"$in": id_list}})
        if id_list
        else 0
    )

    return {
        "label": product_label(twin, spine),
        "ids": id_list,
        "sku": sku,
        "twin": twin,
        "spine": spine,
        "variants": variants,
        "images": images,
        "collection_rows": collection_rows,
        "stock_units": units,
        "order_count": order_count,
        "shopify_product_id": shopify_id_of(twin),
    }


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def refusals_for(target: Dict[str, Any]) -> List[str]:
    """Plain-English reasons this product must NOT be hard-deleted. An empty
    list means it is safe to remove."""
    reasons: List[str] = []

    gid = target["shopify_product_id"]
    if gid:
        reasons.append(
            f"it is on Shopify ({gid}). Take the listing down on Shopify first -- "
            "this script does not call Shopify, and deleting the IMS row here "
            "would strand the live listing."
        )

    if target["order_count"]:
        reasons.append(
            f"it appears on {target['order_count']} order(s). Deleting it would "
            "gut those invoices. It can be deactivated, never hard-deleted."
        )

    stuck = unavailable_units(target["stock_units"])
    if stuck:
        statuses = sorted({str(unit.get("status") or "unknown") for unit in stuck})
        reasons.append(
            f"{len(stuck)} of its {len(target['stock_units'])} stock unit(s) are "
            f"not available stock (status: {', '.join(statuses)}). Settle those units first."
        )

    return reasons


# ---------------------------------------------------------------------------
# Reporting, snapshot, delete
# ---------------------------------------------------------------------------

def describe(target: Dict[str, Any]) -> None:
    """Print one product's blast radius."""
    logger.info("  %s", target["label"])
    logger.info("    ids                 : %s", ", ".join(target["ids"]) or "none")
    logger.info("    catalog_products    : 1")
    logger.info("    products (spine)    : %s", 1 if target["spine"] else 0)
    logger.info("    catalog_variants    : %s", len(target["variants"]))
    logger.info("    product_images      : %s", len(target["images"]))
    logger.info("    collection_products : %s", len(target["collection_rows"]))
    logger.info("    stock_units         : %s", len(target["stock_units"]))


def build_snapshot(targets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every document that is about to be deleted, grouped by collection, in a
    shape that can be inserted straight back."""
    snapshot: Dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "products": [
            {"label": t["label"], "ids": t["ids"], "sku": t["sku"]} for t in targets
        ],
        "documents": {
            "catalog_products": [t["twin"] for t in targets],
            "products": [t["spine"] for t in targets if t["spine"]],
            "catalog_variants": [row for t in targets for row in t["variants"]],
            "product_images": [row for t in targets for row in t["images"]],
            "collection_products": [row for t in targets for row in t["collection_rows"]],
            "stock_units": [row for t in targets for row in t["stock_units"]],
        },
    }
    return snapshot


def write_snapshot(snapshot: Dict[str, Any], directory: str) -> str:
    """Persist the snapshot; returns the path. Raises on failure -- a delete
    without a snapshot is not allowed to happen."""
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(directory, f"catalog_delete_{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, ensure_ascii=False, default=str)
    return path


def delete_target(db: Any, target: Dict[str, Any]) -> Dict[str, int]:
    """Remove one product and its rows. Children go first so an interrupted
    run leaves the product itself (and therefore this script's own selector)
    intact rather than orphaning the children."""
    removed: Dict[str, int] = {}

    def _drop(collection: str, docs: List[Dict[str, Any]]) -> None:
        ids = [doc["_id"] for doc in docs if doc.get("_id") is not None]
        if not ids:
            removed[collection] = 0
            return
        result = db[collection].delete_many({"_id": {"$in": ids}})
        removed[collection] = int(result.deleted_count)

    _drop("stock_units", target["stock_units"])
    _drop("collection_products", target["collection_rows"])
    _drop("product_images", target["images"])
    _drop("catalog_variants", target["variants"])
    _drop("products", [target["spine"]] if target["spine"] else [])
    _drop("catalog_products", [target["twin"]])
    return removed


def write_audit_row(db: Any, targets: List[Dict[str, Any]], snapshot_path: str, actor: str) -> None:
    """One immutable audit row for the whole run. Fail-soft: the delete has
    already happened and must be reported truthfully either way."""
    try:
        db["audit_logs"].insert_one(
            {
                "timestamp": datetime.now(timezone.utc),
                "severity": "CRITICAL",
                "action": AUDIT_ACTION,
                "entity_type": "catalog_product",
                "entity_id": ",".join(t["ids"][0] for t in targets if t["ids"]),
                "user_id": actor,
                "diff": None,
                "context": {
                    "reason": "owner-authorised hard delete so the same items can be re-catalogued",
                    "script": "scripts/delete_catalog_products.py",
                    "snapshot": snapshot_path,
                    "products": [
                        {"label": t["label"], "ids": t["ids"], "sku": t["sku"]}
                        for t in targets
                    ],
                },
            }
        )
        logger.info("Audit row written (%s).", AUDIT_ACTION)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Audit row could NOT be written: %s", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hard-delete catalogue products so the same items can be re-added.",
    )
    selector = parser.add_argument_group("what to delete (at least one required)")
    selector.add_argument("--all", action="store_true", help="every product in the catalogue")
    selector.add_argument("--id", default="", help="comma-separated catalog product ids")
    selector.add_argument("--sku", default="", help="comma-separated SKUs")
    parser.add_argument(
        "--expect",
        type=int,
        default=None,
        help="abort unless exactly this many products are targeted (REQUIRED with --all)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="actually delete. Without this the run is a dry run and writes nothing.",
    )
    parser.add_argument(
        "--snapshot-dir",
        default="catalog_delete_snapshots",
        help="where the pre-delete JSON snapshot is written (default: ./catalog_delete_snapshots)",
    )
    parser.add_argument(
        "--actor",
        default="script:delete_catalog_products",
        help="who to record in the audit row",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    ids = parse_csv_option(args.id)
    skus = parse_csv_option(args.sku)

    if not args.all and not ids and not skus:
        logger.error("Nothing selected. Pass --all, --id or --sku.")
        return 1
    if args.all and (ids or skus):
        logger.error("--all cannot be combined with --id / --sku.")
        return 1
    if args.all and args.expect is None:
        logger.error("--all requires --expect N (the number of products you expect to delete).")
        return 1

    db = connect()
    if db is None:
        return 1

    twins = find_twins(db, want_all=args.all, ids=ids, skus=skus)
    if not twins:
        logger.error("No catalogue products matched. Nothing to do.")
        return 1

    if args.expect is not None and len(twins) != args.expect:
        logger.error(
            "REFUSED: expected %s product(s), found %s. Check the selection before re-running.",
            args.expect,
            len(twins),
        )
        for twin in twins:
            logger.error("  found: %s", product_label(twin, None))
        return 1

    targets = [collect_target(db, twin) for twin in twins]

    logger.info("=" * 72)
    logger.info("Targeted for HARD DELETE: %s product(s)", len(targets))
    logger.info("=" * 72)
    for target in targets:
        describe(target)

    # Every refusal is collected across every target BEFORE any write, so the
    # run is all-or-nothing: a bad product in the list stops the whole batch.
    blocked = [(t, refusals_for(t)) for t in targets]
    blocked = [(t, reasons) for t, reasons in blocked if reasons]
    if blocked:
        logger.error("=" * 72)
        logger.error("REFUSED -- nothing was deleted. %s product(s) cannot be hard-deleted:", len(blocked))
        for target, reasons in blocked:
            logger.error("  %s", target["label"])
            for reason in reasons:
                logger.error("    - %s", reason)
        logger.error("=" * 72)
        return 1

    if not args.commit:
        logger.info("=" * 72)
        logger.info("DRY RUN -- nothing was written. Re-run with --commit to delete.")
        logger.info("=" * 72)
        return 0

    snapshot = build_snapshot(targets)
    try:
        snapshot_path = write_snapshot(snapshot, args.snapshot_dir)
    except OSError as exc:
        logger.error("REFUSED: snapshot could not be written (%s). Nothing was deleted.", exc)
        return 1
    logger.info("Snapshot written: %s", snapshot_path)

    totals: Dict[str, int] = {}
    for target in targets:
        removed = delete_target(db, target)
        for collection, count in removed.items():
            totals[collection] = totals.get(collection, 0) + count
        logger.info("Deleted %s", target["label"])

    logger.info("=" * 72)
    logger.info("DELETED (documents removed per collection):")
    for collection in sorted(totals):
        logger.info("  %-20s %s", collection, totals[collection])
    logger.info("=" * 72)

    write_audit_row(db, targets, snapshot_path, args.actor)
    logger.info("Done. The same items can now be catalogued again through Add-a-Product.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
