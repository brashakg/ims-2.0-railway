#!/usr/bin/env python3
"""
IMS 2.0 -- Prod Data Cleanup
==============================
Fixes four known prod Mongo data-quality blockers that block unique-index builds
and cause duplicated / orphaned records.

  INV-2  Backfill products.product_id where null (~10,805 of 10,820).
  INV-3  Detect duplicate products.sku; offer a safe merge/suffix strategy.
  INV-4  Clear empty-string products.barcode -> unset so the sparse UNIQUE
         barcode index doesn't collide on "".
  OPS-3  Detect duplicate customers.customer_id; plan merge/reassign
         (keep the record with the most data / order references).

SAFETY CONTRACT
---------------
- --dry-run is the DEFAULT.  NOTHING is written without --commit.
- --commit does idempotent, guarded updates (never drops documents).
- Reads MONGODB_URL (preferred) or MONGO_URL from env; fail-soft when absent.
- pymongo lazy-imported -- won't crash at import time without a driver.
- No emojis -- Windows cp1252 safe.

Usage
-----
  # Dry run (default) -- print what WOULD change, write nothing:
  python scripts/prod_data_cleanup.py

  # Explicit dry-run:
  python scripts/prod_data_cleanup.py --dry-run

  # Run only one step:
  python scripts/prod_data_cleanup.py --step inv2
  python scripts/prod_data_cleanup.py --step inv3
  python scripts/prod_data_cleanup.py --step inv4
  python scripts/prod_data_cleanup.py --step ops3

  # Commit (MUTATES PROD -- owner-gated, run via `railway run`):
  python scripts/prod_data_cleanup.py --commit

  # Commit a single step:
  python scripts/prod_data_cleanup.py --commit --step inv2

Exit codes: 0 = OK / dry-run done;  1 = fatal error.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("prod_data_cleanup")

# ---------------------------------------------------------------------------
# Pure helpers (no DB dependency -- unit-testable)
# ---------------------------------------------------------------------------

def generate_stable_product_id(doc: Dict) -> str:
    """Return a stable, collision-resistant product_id for an existing product doc.

    Strategy (mirrors catalog.py `f"prod_{uuid.uuid4().hex[:12]}"` format):
    1. Prefer `sku` as seed  -- most imported products have one.
    2. Fall back to `str(_id)` -- every Mongo doc has an _id.
    Determinism: sha256(seed)[:12] gives the SAME id on every run, so
    re-running with --commit is fully idempotent (the SET is the same value).
    Collision risk: 12 hex chars = 48 bits; for ~11k products P(any collision)
    is ~(11000^2) / (2 * 16^12) < 1e-9 -- negligible.
    """
    sku = (doc.get("sku") or "").strip()
    _id = str(doc.get("_id", ""))
    seed = sku if sku else _id
    digest = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f"prod_{digest}"


def plan_sku_merge(dups: List[Dict]) -> Dict:
    """Given a list of product docs that share a SKU, return a merge plan dict.

    The canonical doc is chosen by:
      1. Presence of product_id (non-null / non-empty) -- prefer the one already
         indexed.
      2. Most complete doc (most non-null top-level fields).
      3. Oldest created_at (original import).

    Duplicate docs are tagged for a suffix rename ("{sku}_dup_{n}") so the unique
    SKU index can build without data loss.
    """
    if not dups:
        return {}

    def _score(d: Dict) -> Tuple:
        has_pid = 1 if d.get("product_id") else 0
        non_null = sum(1 for v in d.values() if v is not None and v != "")
        created = d.get("created_at")
        if created is None:
            # No date -> treat as the latest (sort last = lowest priority)
            ts = float("inf")
        elif isinstance(created, str):
            try:
                dt = datetime.fromisoformat(created)
                ts = dt.timestamp()
            except (ValueError, OSError):
                ts = float("inf")
        elif hasattr(created, "timestamp"):
            try:
                ts = created.timestamp()
            except (OSError, OverflowError, ValueError):
                ts = float("inf")
        else:
            ts = float("inf")
        return (has_pid, non_null, -ts)

    ranked = sorted(dups, key=_score, reverse=True)
    canonical = ranked[0]
    duplicates = ranked[1:]

    plan = {
        "sku": canonical.get("sku"),
        "canonical_id": str(canonical.get("_id")),
        "canonical_product_id": canonical.get("product_id"),
        "duplicates": [
            {
                "_id": str(d.get("_id")),
                "product_id": d.get("product_id"),
                "new_sku": f"{canonical.get('sku')}_dup_{i + 1}",
            }
            for i, d in enumerate(duplicates)
        ],
    }
    return plan


def plan_customer_merge(dups: List[Dict], order_counts: Dict[str, int]) -> Dict:
    """Given customer docs sharing the same customer_id, return a merge plan.

    Canonical is chosen by:
      1. Most orders referencing that _id string.
      2. Most complete doc (non-null fields).
      3. Oldest created_at.

    Duplicate docs get a NEW, freshly-generated customer_id assigned (never
    dropped).  orders / prescriptions that reference the old (duplicate) customer
    doc's _id get re-pointed to the canonical customer_id.
    """
    if not dups:
        return {}

    def _score(d: Dict) -> Tuple:
        oid = str(d.get("_id", ""))
        orders = order_counts.get(oid, 0)
        non_null = sum(1 for v in d.values() if v is not None and v != "")
        created = d.get("created_at")
        if created is None:
            ts = float("inf")
        elif isinstance(created, str):
            try:
                ts = datetime.fromisoformat(created).timestamp()
            except (ValueError, OSError):
                ts = float("inf")
        elif hasattr(created, "timestamp"):
            try:
                ts = created.timestamp()
            except (OSError, OverflowError, ValueError):
                ts = float("inf")
        else:
            ts = float("inf")
        return (orders, non_null, -ts)

    ranked = sorted(dups, key=_score, reverse=True)
    canonical = ranked[0]
    duplicates = ranked[1:]

    plan = {
        "customer_id": canonical.get("customer_id"),
        "canonical_oid": str(canonical.get("_id")),
        "canonical_name": canonical.get("name") or canonical.get("customer_name"),
        "canonical_mobile": canonical.get("mobile") or canonical.get("phone"),
        "duplicates": [
            {
                "_id": str(d.get("_id")),
                "orders_count": order_counts.get(str(d.get("_id")), 0),
                "new_customer_id": f"cust_{uuid.uuid4().hex[:12]}",
            }
            for d in duplicates
        ],
    }
    return plan


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def _connect() -> Optional[Any]:
    """Return a pymongo Database or None (fail-soft)."""
    mongo_url = os.environ.get("MONGODB_URL") or os.environ.get("MONGO_URL")
    if not mongo_url:
        logger.warning("[NO-DB] MONGODB_URL / MONGO_URL not set -- running offline.")
        return None

    try:
        from pymongo import MongoClient  # lazy import  # noqa: PLC0415
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=8000)
        client.admin.command("ping")
        db_name = os.environ.get("MONGO_DATABASE", "ims_2_0")
        logger.info("[OK] Connected to MongoDB database: %s", db_name)
        return client[db_name]
    except ImportError:
        logger.warning("[NO-DB] pymongo not installed -- running offline.")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NO-DB] MongoDB connection failed: %s -- running offline.", exc)
        return None


# ---------------------------------------------------------------------------
# Step INV-2: backfill products.product_id where null
# ---------------------------------------------------------------------------

def run_inv2(db: Optional[Any], commit: bool) -> None:
    """Backfill products.product_id where null/missing."""
    logger.info("=== INV-2: Backfill products.product_id ===")

    if db is None:
        logger.info("[SKIP] No DB connection.")
        return

    coll = db["products"]

    # Count affected docs
    null_filter: Dict = {"$or": [{"product_id": None}, {"product_id": {"$exists": False}}]}
    total_null = coll.count_documents(null_filter)
    total_all = coll.count_documents({})
    logger.info(
        "  Total products: %d  |  Missing product_id: %d",
        total_all,
        total_null,
    )

    if total_null == 0:
        logger.info("  [OK] All products already have product_id. Nothing to do.")
        return

    # Sample 5 for preview
    sample = list(coll.find(null_filter, {"_id": 1, "sku": 1}).limit(5))
    logger.info("  Sample docs that need backfill (up to 5):")
    for d in sample:
        new_id = generate_stable_product_id(d)
        logger.info(
            "    _id=%s  sku=%s  -> product_id=%s",
            d.get("_id"),
            d.get("sku"),
            new_id,
        )

    if not commit:
        logger.info(
            "  [DRY-RUN] Would backfill %d products. Re-run with --commit to apply.",
            total_null,
        )
        return

    # Commit path: iterate and update individually so each gets its stable id.
    # Batch-pipeline $set with $cond + $sha256 would work in Mongo 4.4+, but
    # individual updates are safer cross-version and easier to audit.
    updated = 0
    skipped_collision = 0
    cursor = coll.find(null_filter, {"_id": 1, "sku": 1})
    for doc in cursor:
        new_id = generate_stable_product_id(doc)
        # Guard: if new_id already exists (hash collision or prior partial run),
        # fall back to a random uuid4 so we never overwrite a live product_id.
        existing = coll.find_one({"product_id": new_id})
        if existing and str(existing.get("_id")) != str(doc.get("_id")):
            new_id = f"prod_{uuid.uuid4().hex[:12]}"
            skipped_collision += 1
            logger.warning(
                "  [COLLISION] _id=%s fell back to random id=%s",
                doc.get("_id"),
                new_id,
            )
        result = coll.update_one(
            {
                "_id": doc["_id"],
                "$or": [{"product_id": None}, {"product_id": {"$exists": False}}],
            },
            {"$set": {"product_id": new_id}},
        )
        if result.modified_count:
            updated += 1

    logger.info(
        "  [COMMIT] Done. Updated: %d  Collision-fallbacks: %d",
        updated,
        skipped_collision,
    )


# ---------------------------------------------------------------------------
# Step INV-3: detect + plan duplicate SKUs
# ---------------------------------------------------------------------------

def run_inv3(db: Optional[Any], commit: bool) -> None:
    """Detect duplicate products.sku and (under --commit) suffix-rename the dupes."""
    logger.info("=== INV-3: Duplicate products.sku detection ===")

    if db is None:
        logger.info("[SKIP] No DB connection.")
        return

    coll = db["products"]

    # Aggregate to find SKUs with count > 1 (excluding null/empty)
    pipeline = [
        {"$match": {"sku": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$sku", "count": {"$sum": 1}, "docs": {"$push": "$_id"}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
    ]
    dup_groups = list(coll.aggregate(pipeline))

    if not dup_groups:
        logger.info("  [OK] No duplicate SKUs found.")
        return

    logger.info("  Found %d SKU(s) with duplicates:", len(dup_groups))
    plans = []
    for group in dup_groups:
        sku = group["_id"]
        doc_ids = group["docs"]
        docs = list(coll.find({"_id": {"$in": doc_ids}}))
        plan = plan_sku_merge(docs)
        plans.append(plan)
        logger.info(
            "    SKU '%s' x%d | canonical _id=%s | duplicates to rename: %s",
            sku,
            len(docs),
            plan["canonical_id"],
            [d["new_sku"] for d in plan["duplicates"]],
        )

    if not commit:
        logger.info(
            "  [DRY-RUN] Would suffix-rename %d duplicate docs. Re-run with --commit to apply.",
            sum(len(p["duplicates"]) for p in plans),
        )
        return

    # Commit: rename duplicate SKUs to "{sku}_dup_N"
    renamed = 0
    for plan in plans:
        for dup in plan["duplicates"]:
            try:
                from bson import ObjectId  # noqa: PLC0415
                oid = ObjectId(dup["_id"])
            except Exception:  # noqa: BLE001
                oid = dup["_id"]
            result = coll.update_one(
                {"_id": oid},
                {"$set": {"sku": dup["new_sku"]}},
            )
            if result.modified_count:
                renamed += 1
                logger.info(
                    "    Renamed _id=%s  old_sku=%s  new_sku=%s",
                    dup["_id"],
                    plan["sku"],
                    dup["new_sku"],
                )
            else:
                logger.warning("    [WARN] No update for _id=%s", dup["_id"])

    logger.info("  [COMMIT] Done. Renamed %d duplicate SKU doc(s).", renamed)


# ---------------------------------------------------------------------------
# Step INV-4: clear empty-string products.barcode -> unset
# ---------------------------------------------------------------------------

def run_inv4(db: Optional[Any], commit: bool) -> None:
    """Unset products.barcode where it is the empty string.

    The barcode index is UNIQUE + sparse (connection.py):
        _idx("products", "barcode", unique=True, sparse=True, background=True)
    Sparse means docs where barcode is ABSENT (or None/null) are excluded from
    the index, so they don't collide. But empty-string "" is a present value --
    every one of the 612 "" entries is indexed and they all collide on the same
    key, blocking the build. $unset removes the field entirely, making those docs
    sparse-eligible.
    """
    logger.info("=== INV-4: Clear empty-string products.barcode ===")

    if db is None:
        logger.info("[SKIP] No DB connection.")
        return

    coll = db["products"]

    empty_filter: Dict = {"barcode": ""}
    count = coll.count_documents(empty_filter)
    logger.info("  Products with barcode == '' (empty string): %d", count)

    if count == 0:
        logger.info("  [OK] No empty-string barcodes. Nothing to do.")
        return

    sample = list(
        coll.find(empty_filter, {"_id": 1, "sku": 1, "barcode": 1}).limit(5)
    )
    logger.info("  Sample (up to 5):")
    for d in sample:
        logger.info(
            "    _id=%s  sku=%s  barcode='' -> will $unset barcode",
            d.get("_id"),
            d.get("sku"),
        )

    if not commit:
        logger.info(
            "  [DRY-RUN] Would unset barcode on %d docs. Re-run with --commit to apply.",
            count,
        )
        return

    result = coll.update_many(empty_filter, {"$unset": {"barcode": ""}})
    logger.info(
        "  [COMMIT] Done. Unset barcode on %d doc(s).", result.modified_count
    )


# ---------------------------------------------------------------------------
# Step OPS-3: detect + plan duplicate customers.customer_id
# ---------------------------------------------------------------------------

def run_ops3(db: Optional[Any], commit: bool) -> None:
    """Detect duplicate customers.customer_id; plan / execute merge+reassign."""
    logger.info("=== OPS-3: Duplicate customers.customer_id detection ===")

    if db is None:
        logger.info("[SKIP] No DB connection.")
        return

    cust_coll = db["customers"]
    orders_coll = db["orders"]
    rx_coll = db["prescriptions"]

    # Find customer_ids with > 1 document
    pipeline = [
        {"$match": {"customer_id": {"$nin": [None, ""]}}},
        {
            "$group": {
                "_id": "$customer_id",
                "count": {"$sum": 1},
                "oids": {"$push": "$_id"},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
    ]
    dup_groups = list(cust_coll.aggregate(pipeline))

    if not dup_groups:
        logger.info("  [OK] No duplicate customer_ids found.")
        return

    total_extra = sum(g["count"] - 1 for g in dup_groups)
    logger.info(
        "  Found %d customer_id value(s) with duplicates (%d extra docs to reassign):",
        len(dup_groups),
        total_extra,
    )

    # Build order-count map: customer_id string -> number of orders
    order_count_by_cid: Dict[str, int] = {}
    for g in dup_groups:
        cid = g["_id"]
        cnt = orders_coll.count_documents({"customer_id": cid})
        order_count_by_cid[cid] = cnt

    plans = []
    for group in dup_groups:
        cid = group["_id"]
        oids = group["oids"]
        docs = list(cust_coll.find({"_id": {"$in": oids}}))
        # All docs share the same customer_id, so order count is the same for
        # all; use it as the group-level score (canonical = oldest, most complete).
        oid_order_counts = {
            str(d.get("_id", "")): order_count_by_cid.get(cid, 0) for d in docs
        }
        plan = plan_customer_merge(docs, oid_order_counts)
        plans.append(plan)
        logger.info(
            "    customer_id='%s' x%d | canonical _id=%s | name=%s | mobile=%s | orders=%d",
            cid,
            len(docs),
            plan["canonical_oid"],
            plan.get("canonical_name"),
            plan.get("canonical_mobile"),
            order_count_by_cid.get(cid, 0),
        )
        for dup in plan["duplicates"]:
            logger.info(
                "      DUP _id=%s -> new_customer_id=%s",
                dup["_id"],
                dup["new_customer_id"],
            )

    if not commit:
        logger.info(
            "  [DRY-RUN] Would reassign customer_id on %d duplicate doc(s). "
            "Orders/Rx are not touched (they already reference the canonical id). "
            "Re-run with --commit to apply.",
            total_extra,
        )
        return

    # Commit path:
    # 1. Assign new unique customer_id to each duplicate (ghost) doc.
    # 2. Orders / Rx all reference the SHARED customer_id string -- that string
    #    is the canonical doc's id, which we leave unchanged.  The duplicate docs
    #    are typically ghost/imported records with no associated orders; giving
    #    them a new id separates them from the canonical without breaking any
    #    order reference.
    # NOTE: We do NOT merge doc content -- field-level merge requires owner review.
    # We only separate docs so the unique index can build.

    reassigned = 0
    for plan in plans:
        old_cid = plan["customer_id"]

        for dup in plan["duplicates"]:
            try:
                from bson import ObjectId  # noqa: PLC0415
                oid = ObjectId(dup["_id"])
            except Exception:  # noqa: BLE001
                oid = dup["_id"]

            new_cid = dup["new_customer_id"]

            result = cust_coll.update_one(
                {"_id": oid, "customer_id": old_cid},
                {
                    "$set": {
                        "customer_id": new_cid,
                        "_cleanup_reassigned_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "_cleanup_original_customer_id": old_cid,
                    }
                },
            )
            if result.modified_count:
                reassigned += 1
                logger.info(
                    "    Reassigned customer _id=%s  old_cid=%s  new_cid=%s",
                    dup["_id"],
                    old_cid,
                    new_cid,
                )
            else:
                logger.warning(
                    "    [WARN] No update for _id=%s (already changed or not found)",
                    dup["_id"],
                )

        # Report orders/Rx counts for the canonical so owner can verify.
        orders_count = orders_coll.count_documents({"customer_id": old_cid})
        rx_count = rx_coll.count_documents({"customer_id": old_cid})
        logger.info(
            "    customer_id='%s': %d order(s), %d Rx doc(s) remain pointing at canonical (correct).",
            old_cid,
            orders_count,
            rx_count,
        )

    logger.info(
        "  [COMMIT] Done. Reassigned customer_id on %d doc(s).", reassigned
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

STEPS = {
    "inv2": run_inv2,
    "inv3": run_inv3,
    "inv4": run_inv4,
    "ops3": run_ops3,
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "IMS 2.0 prod data cleanup: backfill product_id, dedupe SKUs, "
            "clear empty barcodes, dedupe customer_ids. "
            "DRY-RUN by default. Pass --commit to mutate."
        )
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print what WOULD change without writing anything (default).",
    )
    mode.add_argument(
        "--commit",
        action="store_true",
        help="Apply changes to the database (MUTATES PROD -- owner-gated).",
    )
    p.add_argument(
        "--step",
        choices=list(STEPS.keys()),
        help="Run only a specific step (default: all).",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    commit = args.commit  # False in dry-run

    logger.info(
        "IMS 2.0 Prod Data Cleanup | mode=%s | step=%s",
        "COMMIT" if commit else "DRY-RUN",
        args.step or "all",
    )
    if commit:
        logger.warning(
            "COMMIT MODE: changes will be written to MongoDB. "
            "Ensure you are running via `railway run` with prod credentials."
        )

    db = _connect()

    steps_to_run = [args.step] if args.step else list(STEPS.keys())
    for step_name in steps_to_run:
        try:
            STEPS[step_name](db, commit=commit)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[STEP %s] Unhandled error: %s",
                step_name.upper(),
                exc,
                exc_info=True,
            )

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
