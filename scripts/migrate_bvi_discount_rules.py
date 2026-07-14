#!/usr/bin/env python3
"""
IMS 2.0 -- BVI Discount-Rule Migration: Postgres (BVI) -> Mongo (IMS)
====================================================================
Migrates BVI's automatic online-discount rules from the Next.js/Postgres
``DiscountRule`` table into the IMS ``ecom_discount_rules`` collection that the
online discount engine (api/services/online_discount_engine.py) reads.

  BVI DiscountRule (Postgres)          ->  ecom_discount_rules (Mongo)
  ---------------------------------        ----------------------------------
  category  (BVI enum, e.g. SUNGLASSES)->  category  (IMS canonical, e.g. SUNGLASS)
  brand                                ->  brand
  subBrand                             ->  sub_brand
  discountPercentage                   ->  discount_percentage
  (no column)                          ->  active   = True   (BVI had none)
  (no column)                          ->  priority = 0      (BVI had none)

FLAG (BVI vs the owner ruling): BVI's DiscountRule model has NO ``active`` and NO
``priority`` column -- every rule was live and specificity+first-match decided the
winner. The IMS engine adds both; this migration DEFAULTS active=True, priority=0
so behaviour is preserved. The owner can then toggle/prioritise going forward.

CATEGORY MAPPING: BVI categories (SPECTACLES / SUNGLASSES / ...) are mapped to the
IMS canonical category (FRAME / SUNGLASS / ...) via api/services/ecom_category_map
(same bridge migrate_bvi_pim.py uses), so a rule's category matches the IMS
catalog_products.category the engine compares against. An unmapped BVI category is
kept verbatim (the engine's resolve_category still self-matches it) and flagged.

SAFETY CONTRACT (mirrors migrate_bvi_pim.py)
--------------------------------------------
- Read-ONLY on Postgres; never writes/deletes a BVI row.
- --dry-run is the DEFAULT. Nothing is written to Mongo unless you pass --commit.
- IDEMPOTENT: upsert keyed on the natural (category, brand, sub_brand) triple
  (normalised), so re-running with --commit is safe and never duplicates.
- Requires ECOMMERCE_DATABASE_URL (BVI Postgres) + MONGODB_URL / MONGO_URL (IMS).
- psycopg2 + pymongo are lazy-imported so a missing driver only fails its own path.
- No emojis (Windows cp1252 safe).

Usage
-----
  python scripts/migrate_bvi_discount_rules.py --dry-run      # default: writes nothing
  python scripts/migrate_bvi_discount_rules.py --commit       # live upsert
  python scripts/migrate_bvi_discount_rules.py --commit --pg-url postgresql://... \\
    --mongo-url mongodb://... --db ims_2_0

Exit codes: 0 = success / dry-run OK; 1 = fatal connection/import error.
"""
from __future__ import annotations

import argparse
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
logger = logging.getLogger("migrate_bvi_discount_rules")

RULES_COLLECTION = "ecom_discount_rules"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _backend_on_path() -> None:
    """Make backend/ importable so the canonical category bridge can be reused
    (mirrors migrate_bvi_pim.py._backend_on_path)."""
    _here = os.path.dirname(os.path.abspath(__file__))
    _backend = os.path.join(os.path.dirname(_here), "backend")
    if _backend not in sys.path:
        sys.path.insert(0, _backend)


# Inline fallback bridge, used ONLY when the backend package is unavailable
# (standalone run). Mirrors api/services/ecom_category_map._TABLE for the
# categories a DiscountRule realistically carries.
_BVI_TO_IMS_FALLBACK: Dict[str, str] = {
    "SPECTACLES": "FRAME",
    "SUNGLASSES": "SUNGLASS",
    "LENSES": "OPTICAL_LENS",
    "CONTACT_LENSES": "CONTACT_LENS",
    "COLOR_CONTACT_LENSES": "COLORED_CONTACT_LENS",
    "READING_GLASSES": "READING_GLASSES",
    "WATCHES": "WATCH",
    "SMARTWATCHES": "SMARTWATCH",
    "SMARTGLASSES": "SMARTGLASSES",
    "CLOCKS": "WALL_CLOCK",
    "ACCESSORIES": "ACCESSORIES",
    "SOLUTIONS": "ACCESSORIES",
    "SERVICES": "SERVICES",
}


def map_bvi_category(bvi_category: Any) -> Tuple[str, bool]:
    """BVI category -> (IMS category, unmapped_flag). Uses the canonical bridge;
    an unknown category is kept verbatim (uppercased) + flagged so the engine's
    resolve_category still self-matches it rather than silently dropping the rule."""
    key = _str(bvi_category).upper().replace("-", "_").replace(" ", "_")
    if not key:
        return "", True
    try:
        _backend_on_path()
        from api.services.ecom_category_map import bvi_to_ims, is_known_bvi  # noqa: PLC0415

        if is_known_bvi(key):
            return bvi_to_ims(key), False
        return key, True
    except ImportError:
        if key in _BVI_TO_IMS_FALLBACK:
            return _BVI_TO_IMS_FALLBACK[key], False
        return key, True


# ---------------------------------------------------------------------------
# Pure mapper (unit-testable without a DB)
# ---------------------------------------------------------------------------

def map_discount_rule(row: Dict) -> Dict:
    """Pure mapper: BVI DiscountRule PG row -> IMS ecom_discount_rules doc.

    Key design decisions:
    - category mapped to the IMS canonical value (so it matches catalog_products
      .category the engine compares against); unmapped kept verbatim + flagged.
    - brand / subBrand carried through verbatim (may be empty -> a broader rule).
    - active defaults True, priority defaults 0 (BVI had NEITHER column).
    - natural key fields (category/brand/sub_brand) drive the idempotent upsert.
    """
    ims_category, unmapped = map_bvi_category(row.get("category"))
    # Store the NORMALISED form (category UPPER canonical, brand/sub_brand lower)
    # so it matches natural_key() exactly -> the upsert filter re-matches on a
    # re-run (idempotent) and stays consistent with the CRUD router, which writes
    # the same normalised shape. The engine matches case-insensitively regardless.
    brand = _str(row.get("brand")).lower()
    sub_brand = _str(row.get("subBrand")).lower()

    doc: Dict[str, Any] = {
        "category": _str(ims_category).upper(),
        "brand": brand,
        "sub_brand": sub_brand,
        "discount_percentage": _float(row.get("discountPercentage")),
        # BVI had no active/priority; default to live + neutral priority.
        "active": True,
        "priority": 0,
        # Audit / reversibility
        "bvi_category": _str(row.get("category")),
        "bvi_rule_id": _str(row.get("id")),
        "source": "bvi_migration",
        "migrated_at": _now_utc(),
    }
    if unmapped:
        doc["category_unmapped"] = True
    return doc


def natural_key(doc: Dict) -> Dict:
    """The idempotent upsert filter -- the (category, brand, sub_brand) triple,
    normalised (upper category / lower brand+sub_brand) so casing variants never
    duplicate. The engine matches case-insensitively too."""
    return {
        "category": _str(doc.get("category")).upper(),
        "brand": _str(doc.get("brand")).lower(),
        "sub_brand": _str(doc.get("sub_brand")).lower(),
    }


# ---------------------------------------------------------------------------
# Postgres + Mongo connections (mirror migrate_bvi_pim.py)
# ---------------------------------------------------------------------------

def _pg_connect(pg_url: str):
    try:
        import psycopg2  # noqa: PLC0415
        import psycopg2.extras  # noqa: PLC0415, F401
    except ImportError as e:
        logger.error("[PG] psycopg2 not available: %s", e)
        return None
    try:
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        conn.set_session(readonly=True, autocommit=True)
        return conn
    except Exception as e:  # noqa: BLE001
        logger.error("[PG] connect failed: %s", e)
        return None


def _pg_fetchall(conn, sql: str, params: Tuple = ()) -> List[Dict]:
    try:
        import psycopg2.extras  # noqa: PLC0415
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.error("[PG] query failed: %s", e)
        return []


def _mongo_connect(mongo_url: str, db_name: str):
    try:
        from pymongo import MongoClient  # noqa: PLC0415
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=10_000)
        client.admin.command("ping")
        return client, client[db_name]
    except ImportError as e:
        logger.error("[MONGO] pymongo not available: %s", e)
        return None, None
    except Exception as e:  # noqa: BLE001
        logger.error("[MONGO] connect failed: %s", e)
        return None, None


def _new_rule_id() -> str:
    """A stable CRUD id for a migrated rule (same shape the router mints:
    online_store_discount_rules.create_rule uses ``rule_{uuid4().hex[:12]}``)."""
    return f"rule_{uuid.uuid4().hex[:12]}"


def _upsert_one(collection, filter_doc: Dict, doc: Dict) -> bool:
    """Idempotent upsert. ``bvi_rule_id`` (the natural creation lineage), the CRUD
    id (``rule_id`` + its ``id`` mirror) and created_at live in $setOnInsert so a
    re-run never shifts them; everything else refreshes each run.

    Minting rule_id/id at INSERT is the fix for BUG-9: without them the router's
    get/update/delete (keyed on rule_id) and the FE list (React key = rule_id) see
    ``undefined`` -> every migrated rule was uneditable/undeletable, so a live
    storefront discount could not be turned off. $setOnInsert keeps them stable
    across re-runs."""
    try:
        now = _now_utc()
        rid = _new_rule_id()
        set_on_insert = {
            "created_at": doc.pop("created_at", now),
            "rule_id": rid,
            "id": rid,
        }
        if doc.get("bvi_rule_id"):
            set_on_insert["bvi_rule_id"] = doc.pop("bvi_rule_id")
        # Never let $set fight $setOnInsert over the same key (a re-run must keep the
        # original id, not rotate it).
        doc.pop("rule_id", None)
        doc.pop("id", None)
        doc["updated_at"] = now
        collection.update_one(
            filter_doc,
            {"$set": doc, "$setOnInsert": set_on_insert},
            upsert=True,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[UPSERT] failed on filter %s: %s", filter_doc, e)
        return False


def _backfill_rule_ids(collection) -> int:
    """Give any pre-existing ecom_discount_rules doc that is missing a CRUD id one
    (covers rows written before the BUG-9 fix so they too become editable/deletable
    via the router + UI). Idempotent + fail-soft; returns the count backfilled."""
    backfilled = 0
    try:
        cursor = collection.find(
            {"$or": [{"rule_id": {"$exists": False}}, {"rule_id": None}, {"rule_id": ""}]}
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[BACKFILL] scan failed: %s", e)
        return 0
    for d in cursor:
        rid = _new_rule_id()
        try:
            collection.update_one(
                {"_id": d.get("_id")}, {"$set": {"rule_id": rid, "id": rid}}
            )
            backfilled += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("[BACKFILL] update failed for %s: %s", d.get("_id"), e)
    if backfilled:
        logger.info("[BACKFILL] minted rule_id for %d pre-existing doc(s)", backfilled)
    return backfilled


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_discount_rules(pg_conn, mongo_db, *, dry_run: bool, sample_n: int = 5) -> Dict:
    logger.info("[RULES] fetching DiscountRule from Postgres...")
    rows = _pg_fetchall(pg_conn, 'SELECT * FROM "DiscountRule" ORDER BY id')
    total = len(rows)
    logger.info("[RULES] %d rows found", total)

    docs = [map_discount_rule(r) for r in rows]
    docs = [d for d in docs if _str(d.get("category"))]  # guard blank-category rows
    unmapped = sum(1 for d in docs if d.get("category_unmapped"))

    if dry_run:
        logger.info(
            "[RULES] [DRY-RUN] would upsert %d docs (keyed on category+brand+sub_brand)",
            len(docs),
        )
        if unmapped:
            logger.info("[RULES] %d rows had an UNMAPPED BVI category (kept verbatim + flagged)", unmapped)
        for d in docs[:sample_n]:
            logger.info(
                "  %s / brand=%s / sub=%s -> %.2f%% (active=%s prio=%s)",
                d.get("category"),
                d.get("brand") or "-",
                d.get("sub_brand") or "-",
                d.get("discount_percentage"),
                d.get("active"),
                d.get("priority"),
            )
        return {"entity": "discount_rules", "pg_rows": total, "upserted": 0, "dry_run": True}

    coll = mongo_db[RULES_COLLECTION]
    upserted = 0
    for d in docs:
        if _upsert_one(coll, natural_key(d), d):
            upserted += 1
    logger.info("[RULES] upserted %d / %d", upserted, len(docs))
    # Backfill CRUD ids for any doc written before the rule_id fix (idempotent).
    backfilled = _backfill_rule_ids(coll)
    return {
        "entity": "discount_rules",
        "pg_rows": total,
        "upserted": upserted,
        "unmapped": unmapped,
        "backfilled": backfilled,
        "dry_run": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Print counts + samples; write NOTHING (default: ON).")
    parser.add_argument("--commit", action="store_true", default=False,
                        help="Actually upsert into Mongo (disables --dry-run). Idempotent.")
    parser.add_argument("--pg-url", default=os.getenv("ECOMMERCE_DATABASE_URL"),
                        help="BVI Postgres URL (default: $ECOMMERCE_DATABASE_URL).")
    parser.add_argument("--mongo-url",
                        default=os.getenv("MONGODB_URL") or os.getenv("MONGO_URL"),
                        help="IMS Mongo URL (default: $MONGODB_URL / $MONGO_URL).")
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"),
                        help="Mongo database name (default: ims_2_0).")
    parser.add_argument("--sample", type=int, default=5,
                        help="Sample docs to print in dry-run (default: 5).")
    args = parser.parse_args()

    dry_run = not args.commit
    mode = "DRY-RUN (no writes)" if dry_run else "COMMIT (live upserts)"
    logger.info("=" * 60)
    logger.info("BVI Discount-Rule Migration  --  mode: %s", mode)
    logger.info("pg_url: %s", "SET" if args.pg_url else "NOT SET")
    logger.info("mongo_url: %s", "SET" if args.mongo_url else "NOT SET")
    logger.info("db: %s", args.db)
    logger.info("=" * 60)

    if not args.pg_url:
        logger.error("ECOMMERCE_DATABASE_URL is not set. Pass --pg-url to connect to BVI Postgres.")
        sys.exit(1)

    pg_conn = _pg_connect(args.pg_url)
    if pg_conn is None:
        logger.error("Cannot connect to BVI Postgres. Aborting.")
        sys.exit(1)
    logger.info("[PG] connected (read-only)")

    mongo_db = None
    mongo_client = None
    if not dry_run:
        if not args.mongo_url:
            logger.error("MONGODB_URL is not set. Pass --mongo-url to connect to IMS Mongo.")
            try:
                pg_conn.close()
            except Exception:  # noqa: BLE001
                pass
            sys.exit(1)
        mongo_client, mongo_db = _mongo_connect(args.mongo_url, args.db)
        if mongo_db is None:
            logger.error("Cannot connect to IMS Mongo. Aborting.")
            try:
                pg_conn.close()
            except Exception:  # noqa: BLE001
                pass
            sys.exit(1)
        logger.info("[MONGO] connected to db=%s", args.db)

    try:
        result = run_discount_rules(pg_conn, mongo_db, dry_run=dry_run, sample_n=args.sample)
    except Exception as e:  # noqa: BLE001
        logger.error("[RULES] UNEXPECTED ERROR: %s", e)
        result = {"entity": "discount_rules", "error": str(e)}

    try:
        pg_conn.close()
    except Exception:  # noqa: BLE001
        pass
    if mongo_client is not None:
        try:
            mongo_client.close()
        except Exception:  # noqa: BLE001
            pass

    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY  --  mode: %s", mode)
    if "error" in result:
        logger.info("discount_rules  ERROR: %s", result["error"])
    else:
        logger.info(
            "discount_rules  pg_rows=%s  upserted=%s",
            result.get("pg_rows", "-"),
            result.get("upserted", "N/A (dry-run)") if result.get("dry_run") else result.get("upserted", 0),
        )
    logger.info("=" * 60)
    if dry_run:
        logger.info("REMINDER: DRY-RUN. Re-run with --commit to write to Mongo.")
    else:
        logger.info("Migration complete. Then run recompute_all to apply rules to catalog prices.")


if __name__ == "__main__":
    main()
