#!/usr/bin/env python3
"""
IMS 2.0 -- BVI Phase 0 Parity Oracle: Postgres (BVI) <-> Mongo (IMS)
====================================================================
The READ-ONLY safety net that runs BEFORE any data moves and AFTER a migration
to prove the IMS Mongo catalog faithfully mirrors the BVI Postgres catalog.

It answers the Phase-1 exit-gate question:
  "Did EVERY BVI SKU + storeBarcode land in IMS catalog_products/catalog_variants,
   with the same barcode?"  (the ~100% SKU+storeBarcode match gate).

WHAT IT COMPARES (per entity):
  * products      -- BVI "Product" count       vs IMS catalog_products count
  * variants      -- BVI "ProductVariant" count vs IMS catalog_variants count
  * collections   -- BVI "Collection" count    vs IMS ecom_collections count
  * menus         -- BVI "Menu" count          vs IMS ecom_menus count
  * customers     -- BVI "Customer" count      vs IMS customers count
  * orders        -- BVI "Order" count         vs IMS online_orders count
  * SKU diff      -- SKUs in BVI ProductVariant missing from IMS catalog_variants
  * barcode diff  -- storeBarcodes whose IMS barcode is missing or MISMATCHED
  * image storage -- count of ProductImage/VariantImage rows whose url is on
                     LOCAL DISK ("/uploads/...") vs a DURABLE host (http(s)://...)
                     -- quantifies the Phase-4 re-host scope from live data.

SAFETY CONTRACT (binding):
  * STRICTLY READ-ONLY. Only SELECT (Postgres) and find/count/aggregate (Mongo).
    No INSERT / UPDATE / DELETE / DDL anywhere. The Postgres session is opened
    read-only (conn.set_session(readonly=True)) so the server itself rejects any
    accidental write.
  * NEVER hardcodes, logs, or prints a connection string or any secret. The URLs
    arrive ONLY from the environment at run time (inject via `railway run`).
    Status lines print "SET" / "NOT SET", never the value.
  * Fails LOUD: a missing env var or an unreachable DB exits NON-ZERO with a clear
    message -- it never crashes silently and never reports false parity.
  * psycopg2 + pymongo are lazy-imported so a missing driver yields a clear
    "pip install ..." message, not an import-time stack trace. psycopg2-binary is
    already a backend dependency (requirements.txt) for the read-only BVI bridge.
  * No emojis -- Windows cp1252 safe.

ENV VARS (read at run time, injected via railway run -- never hardcoded):
  * BVI_DATABASE_URL or ECOMMERCE_DATABASE_URL -- the BVI Postgres connection
    string. ECOMMERCE_DATABASE_URL is what the live IMS backend + the migration
    script already use on Railway; BVI_DATABASE_URL is accepted as an alias.
  * MONGODB_URL or MONGO_URL -- the IMS Mongo connection string.
  * MONGO_DATABASE -- the Mongo database name (default: ims_2_0).

USAGE (see docs/reference/BVI_PHASE0_PREFLIGHT.md for the railway-run runbook):
  # Full report (human-readable text):
  python scripts/bvi_parity_check.py

  # JSON report (machine-readable, for CI / diffing):
  python scripts/bvi_parity_check.py --json

  # Limit how many sample mismatches are listed (default 25):
  python scripts/bvi_parity_check.py --sample 50

Exit codes:
  0 = ran successfully AND parity is within the gate (no missing SKUs, no barcode
      mismatches, counts equal). A clean pre-migration baseline also exits 0
      (everything is "missing" only AFTER migration is expected -- see the report).
  1 = could not connect / missing env / driver missing / a fatal error (FAIL LOUD).
  2 = ran successfully but parity FAILED (missing SKUs or barcode mismatches or a
      count delta) -- the Phase-1 gate is NOT met yet.

The comparison core (build_parity_report) is a PURE function of two in-memory
snapshots, so it is unit-tested without any database
(see backend/tests/test_bvi_parity_check.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bvi_parity_check")


# ===========================================================================
# Pure helpers (no I/O)
# ===========================================================================

def _norm_sku(v: Any) -> str:
    """Normalise a SKU for comparison: strip + upper-case. Empty stays empty."""
    if v is None:
        return ""
    return str(v).strip().upper()


def _norm_barcode(v: Any) -> str:
    """Normalise a barcode for comparison: strip only (barcodes are exact, but
    leading/trailing whitespace from a spreadsheet import is noise)."""
    if v is None:
        return ""
    return str(v).strip()


def is_durable_image_url(url: Any) -> bool:
    """True if the image URL points at a DURABLE host (survives BVI shutdown).

    A durable URL is an absolute http(s):// URL (Shopify CDN, S3, etc.). A
    LOCAL-DISK url is the BVI fallback "/uploads/<file>" (relative path served
    from the Next.js app's own disk) -- it 404s the moment BVI is turned off, so
    every such row is in the Phase-4 re-host scope.

    Anything blank/None is treated as NON-durable (it needs attention too).
    """
    if url is None:
        return False
    s = str(url).strip().lower()
    if not s:
        return False
    return s.startswith("http://") or s.startswith("https://")


# ===========================================================================
# Snapshot dataclasses -- the in-memory shapes the pure comparator consumes
# ===========================================================================

@dataclass
class BviSnapshot:
    """A read-only snapshot of the BVI Postgres catalog (counts + index lists).

    `variant_skus` / `variant_barcodes` are the full lists used for the SKU and
    barcode diffs. The image-url lists drive the storage audit. Everything here
    is derived from SELECTs only.
    """
    products: int = 0
    variants: int = 0
    collections: int = 0
    menus: int = 0
    customers: int = 0
    orders: int = 0
    # variant identity, for the diff: list of (sku, store_barcode)
    variant_skus: List[str] = field(default_factory=list)
    # sku -> store_barcode (only variants that carry a storeBarcode)
    variant_barcode_by_sku: Dict[str, str] = field(default_factory=dict)
    # image urls (both ProductImage.url and VariantImage.url) for the storage audit
    image_urls: List[str] = field(default_factory=list)


@dataclass
class ImsSnapshot:
    """A read-only snapshot of the IMS Mongo catalog (counts + index maps)."""
    catalog_products: int = 0
    catalog_variants: int = 0
    ecom_collections: int = 0
    ecom_menus: int = 0
    customers: int = 0
    online_orders: int = 0
    # the set of SKUs present in catalog_variants (normalised)
    variant_skus: List[str] = field(default_factory=list)
    # sku -> barcode (IMS catalog_variants stores BVI storeBarcode as store_barcode
    # AND/OR the billing spine `products` carries `barcode`); we map sku -> the
    # IMS barcode we can find for that sku
    barcode_by_sku: Dict[str, str] = field(default_factory=dict)


# ===========================================================================
# PURE comparator -- unit-tested without a DB
# ===========================================================================

def build_parity_report(
    bvi: BviSnapshot,
    ims: ImsSnapshot,
    *,
    sample_limit: int = 25,
) -> Dict[str, Any]:
    """Compare a BVI snapshot to an IMS snapshot. PURE -- no I/O.

    Returns a structured report dict:
      {
        "counts": { entity: {bvi, ims, delta, match} ... },
        "sku_diff": {
            "bvi_total", "ims_total",
            "missing_in_ims": [skus...],   (in BVI variants, absent from IMS)
            "missing_count",
            "sample_missing": [...up to sample_limit...],
        },
        "barcode_diff": {
            "checked",                     (BVI variants carrying a storeBarcode)
            "mismatched": [{sku, bvi, ims}...],   (IMS barcode missing OR != BVI)
            "mismatched_count",
            "sample_mismatched": [...up to sample_limit...],
        },
        "image_storage": {
            "total", "durable", "local_disk",
            "local_disk_pct",
            "phase4_rehost_needed": bool,  (any local-disk url -> True)
        },
        "gate_pass": bool,   (counts match AND no missing SKU AND no barcode mismatch)
      }
    """
    # --- counts, per entity ---
    def _count_row(b: int, i: int) -> Dict[str, Any]:
        return {"bvi": b, "ims": i, "delta": i - b, "match": b == i}

    counts = {
        "products": _count_row(bvi.products, ims.catalog_products),
        "variants": _count_row(bvi.variants, ims.catalog_variants),
        "collections": _count_row(bvi.collections, ims.ecom_collections),
        "menus": _count_row(bvi.menus, ims.ecom_menus),
        "customers": _count_row(bvi.customers, ims.customers),
        "orders": _count_row(bvi.orders, ims.online_orders),
    }

    # --- SKU diff (BVI variants missing from IMS catalog_variants) ---
    bvi_sku_set = {_norm_sku(s) for s in bvi.variant_skus if _norm_sku(s)}
    ims_sku_set = {_norm_sku(s) for s in ims.variant_skus if _norm_sku(s)}
    missing_in_ims = sorted(bvi_sku_set - ims_sku_set)
    sku_diff = {
        "bvi_total": len(bvi_sku_set),
        "ims_total": len(ims_sku_set),
        "missing_in_ims": missing_in_ims,
        "missing_count": len(missing_in_ims),
        "sample_missing": missing_in_ims[:sample_limit],
        # informational: SKUs IMS has that BVI does not (NOT a gate failure, but
        # surfaced so a stale/over-migrated IMS row is visible)
        "extra_in_ims": sorted(ims_sku_set - bvi_sku_set)[:sample_limit],
        "extra_in_ims_count": len(ims_sku_set - bvi_sku_set),
    }

    # --- barcode diff (storeBarcode present in BVI must equal IMS barcode) ---
    ims_barcode_by_sku = {
        _norm_sku(k): _norm_barcode(v) for k, v in (ims.barcode_by_sku or {}).items()
    }
    mismatched: List[Dict[str, str]] = []
    checked = 0
    for sku, bvi_bc in (bvi.variant_barcode_by_sku or {}).items():
        nsku = _norm_sku(sku)
        nbc = _norm_barcode(bvi_bc)
        if not nbc:
            continue  # only check variants that actually carry a storeBarcode
        checked += 1
        ims_bc = ims_barcode_by_sku.get(nsku, "")
        if ims_bc != nbc:
            mismatched.append({"sku": nsku, "bvi": nbc, "ims": ims_bc})
    mismatched.sort(key=lambda m: m["sku"])
    barcode_diff = {
        "checked": checked,
        "mismatched": mismatched,
        "mismatched_count": len(mismatched),
        "sample_mismatched": mismatched[:sample_limit],
    }

    # --- image storage audit (from live data) ---
    total_imgs = len(bvi.image_urls)
    durable = sum(1 for u in bvi.image_urls if is_durable_image_url(u))
    local_disk = total_imgs - durable
    image_storage = {
        "total": total_imgs,
        "durable": durable,
        "local_disk": local_disk,
        "local_disk_pct": round((local_disk / total_imgs * 100.0), 2) if total_imgs else 0.0,
        "phase4_rehost_needed": local_disk > 0,
    }

    gate_pass = (
        counts["products"]["match"]
        and counts["variants"]["match"]
        and sku_diff["missing_count"] == 0
        and barcode_diff["mismatched_count"] == 0
    )

    return {
        "counts": counts,
        "sku_diff": sku_diff,
        "barcode_diff": barcode_diff,
        "image_storage": image_storage,
        "gate_pass": gate_pass,
    }


def render_text_report(report: Dict[str, Any]) -> str:
    """Render the parity report dict as a human-readable text block."""
    lines: List[str] = []
    lines.append("=" * 64)
    lines.append("BVI <-> IMS PARITY REPORT (read-only)")
    lines.append("=" * 64)

    lines.append("")
    lines.append("ENTITY COUNTS  (BVI Postgres vs IMS Mongo)")
    lines.append("-" * 64)
    lines.append(f"{'entity':<14}{'bvi':>10}{'ims':>10}{'delta':>10}  {'match':<6}")
    for entity, row in report["counts"].items():
        lines.append(
            f"{entity:<14}{row['bvi']:>10}{row['ims']:>10}{row['delta']:>10}  "
            f"{'OK' if row['match'] else 'DIFF':<6}"
        )

    sd = report["sku_diff"]
    lines.append("")
    lines.append("SKU PARITY  (BVI ProductVariant -> IMS catalog_variants)")
    lines.append("-" * 64)
    lines.append(f"  BVI variant SKUs : {sd['bvi_total']}")
    lines.append(f"  IMS variant SKUs : {sd['ims_total']}")
    lines.append(f"  MISSING in IMS   : {sd['missing_count']}")
    if sd["sample_missing"]:
        lines.append(f"  sample missing   : {', '.join(sd['sample_missing'])}")
    if sd["extra_in_ims_count"]:
        lines.append(f"  extra in IMS     : {sd['extra_in_ims_count']} (not in BVI)")

    bd = report["barcode_diff"]
    lines.append("")
    lines.append("BARCODE PARITY  (BVI storeBarcode -> IMS barcode)")
    lines.append("-" * 64)
    lines.append(f"  checked (have storeBarcode) : {bd['checked']}")
    lines.append(f"  MISMATCHED / missing in IMS : {bd['mismatched_count']}")
    for m in bd["sample_mismatched"]:
        lines.append(
            f"    sku={m['sku']:<20} bvi={m['bvi'] or '(none)':<16} "
            f"ims={m['ims'] or '(none)'}"
        )

    img = report["image_storage"]
    lines.append("")
    lines.append("IMAGE STORAGE  (Phase-4 re-host scope, from live data)")
    lines.append("-" * 64)
    lines.append(f"  total images        : {img['total']}")
    lines.append(f"  durable (http/https): {img['durable']}")
    lines.append(f"  LOCAL DISK /uploads : {img['local_disk']}  ({img['local_disk_pct']}%)")
    lines.append(
        f"  Phase-4 re-host     : "
        f"{'NEEDED (local-disk images present)' if img['phase4_rehost_needed'] else 'not needed (all durable)'}"
    )

    lines.append("")
    lines.append("=" * 64)
    lines.append(f"GATE: {'PASS' if report['gate_pass'] else 'FAIL -- parity not yet met'}")
    lines.append("=" * 64)
    return "\n".join(lines)


# ===========================================================================
# Postgres (read-only) snapshot collection -- I/O, never imported by tests
# ===========================================================================

def _resolve_pg_url() -> Optional[str]:
    """The BVI Postgres URL from env. Prefer BVI_DATABASE_URL (the Phase-0 spec
    name), fall back to ECOMMERCE_DATABASE_URL (what the live backend + the
    migration script use on Railway). NEVER returns/prints the value to a log."""
    return os.getenv("BVI_DATABASE_URL") or os.getenv("ECOMMERCE_DATABASE_URL")


def _resolve_mongo_url() -> Optional[str]:
    return os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")


def _pg_connect(pg_url: str):
    """Open a SHORT-LIVED, READ-ONLY Postgres connection, or None on failure.

    Mirrors online_catalog._connect / migrate_bvi_pim._pg_connect. The session is
    set read-only at the server so even a buggy query cannot write.
    """
    try:
        import psycopg2  # noqa: PLC0415 -- lazy import
        import psycopg2.extras  # noqa: PLC0415 (used by _pg_fetchall)
    except ImportError as e:
        logger.error(
            "psycopg2 is not installed (%s). Install it: pip install psycopg2-binary "
            "(it is already pinned in backend/requirements.txt).",
            e,
        )
        return None
    try:
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        # READ-ONLY at the server: any INSERT/UPDATE/DELETE is rejected by PG.
        conn.set_session(readonly=True, autocommit=True)
        return conn
    except Exception as e:  # noqa: BLE001
        logger.error("Postgres connect failed: %s", e)
        return None


def _pg_count(conn, table: str) -> int:
    """SELECT COUNT(*) for a BVI table (quoted -- Prisma uses PascalCase).
    Returns -1 on error so a failure is visible (never silently 0)."""
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as e:  # noqa: BLE001
        logger.error("count(%s) failed: %s", table, e)
        return -1


def _pg_fetchall(conn, sql: str) -> List[Dict]:
    """Run a SELECT, return list-of-dicts. [] on error (logged)."""
    try:
        import psycopg2.extras  # noqa: PLC0415
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.error("query failed: %s", e)
        return []


def collect_bvi_snapshot(conn) -> BviSnapshot:
    """Read the BVI Postgres catalog into an in-memory snapshot. SELECT-only."""
    snap = BviSnapshot()
    snap.products = _pg_count(conn, "Product")
    snap.variants = _pg_count(conn, "ProductVariant")
    snap.collections = _pg_count(conn, "Collection")
    snap.menus = _pg_count(conn, "Menu")
    snap.customers = _pg_count(conn, "Customer")
    snap.orders = _pg_count(conn, "Order")

    # Variant identity: sku + storeBarcode (the two-barcode model -- storeBarcode
    # is the physical store barcode, the Phase-1 gate key).
    variant_rows = _pg_fetchall(
        conn, 'SELECT sku, "storeBarcode" FROM "ProductVariant"'
    )
    for r in variant_rows:
        sku = _norm_sku(r.get("sku"))
        if not sku:
            continue
        snap.variant_skus.append(sku)
        bc = _norm_barcode(r.get("storeBarcode"))
        if bc:
            snap.variant_barcode_by_sku[sku] = bc

    # Image urls for the storage audit (ProductImage + VariantImage).
    prod_img_rows = _pg_fetchall(conn, 'SELECT url, "originalUrl" FROM "ProductImage"')
    var_img_rows = _pg_fetchall(conn, 'SELECT url, "originalUrl" FROM "VariantImage"')
    for r in prod_img_rows + var_img_rows:
        # Count both url AND originalUrl -- either can be a local-disk path.
        if r.get("url") is not None:
            snap.image_urls.append(str(r.get("url")))
        if r.get("originalUrl"):
            snap.image_urls.append(str(r.get("originalUrl")))

    return snap


# ===========================================================================
# Mongo (read-only) snapshot collection -- I/O
# ===========================================================================

def _mongo_connect(mongo_url: str, db_name: str):
    """Return (client, db) or (None, None). Pings to confirm connectivity."""
    try:
        from pymongo import MongoClient  # noqa: PLC0415
    except ImportError as e:
        logger.error("pymongo is not installed (%s). pip install pymongo.", e)
        return None, None
    try:
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=10_000)
        client.admin.command("ping")
        return client, client[db_name]
    except Exception as e:  # noqa: BLE001
        logger.error("Mongo connect failed: %s", e)
        return None, None


def collect_ims_snapshot(db) -> ImsSnapshot:
    """Read the IMS Mongo catalog into an in-memory snapshot. find/count only.

    IMS collection map (verified against product_master.py + migrate_bvi_pim.py):
      catalog_products  -- the PIM superset (Shopify/BVI lineage)
      catalog_variants  -- the per-SKU variant identity tier (carries store_barcode)
      ecom_collections  -- migrated Collection docs
      ecom_menus        -- migrated Menu docs
      customers         -- IMS customers
      online_orders     -- online-store orders (store-scoped)
    The billing spine `products` also carries `barcode`, so we fold spine barcodes
    into the barcode-by-sku map as a fallback when catalog_variants has none.
    """
    snap = ImsSnapshot()

    def _count(name: str) -> int:
        try:
            return int(db.get_collection(name).count_documents({}))
        except Exception as e:  # noqa: BLE001
            logger.error("Mongo count(%s) failed: %s", name, e)
            return -1

    snap.catalog_products = _count("catalog_products")
    snap.catalog_variants = _count("catalog_variants")
    snap.ecom_collections = _count("ecom_collections")
    snap.ecom_menus = _count("ecom_menus")
    snap.customers = _count("customers")
    snap.online_orders = _count("online_orders")

    # SKU + barcode index from catalog_variants (store_barcode is the migrated
    # BVI storeBarcode; barcode/gtin may also be present).
    try:
        cur = db.get_collection("catalog_variants").find(
            {}, {"sku": 1, "store_barcode": 1, "barcode": 1, "gtin": 1, "_id": 0}
        )
        for d in cur:
            sku = _norm_sku(d.get("sku"))
            if not sku:
                continue
            snap.variant_skus.append(sku)
            bc = _norm_barcode(
                d.get("store_barcode") or d.get("barcode") or d.get("gtin")
            )
            if bc:
                snap.barcode_by_sku[sku] = bc
    except Exception as e:  # noqa: BLE001
        logger.error("Mongo read(catalog_variants) failed: %s", e)

    # Fold in spine `products.barcode` for any sku not already mapped (the billing
    # spine is the source of truth for the physical store barcode in IMS).
    try:
        cur = db.get_collection("products").find(
            {}, {"sku": 1, "barcode": 1, "_id": 0}
        )
        for d in cur:
            sku = _norm_sku(d.get("sku"))
            bc = _norm_barcode(d.get("barcode"))
            if sku and bc and sku not in snap.barcode_by_sku:
                snap.barcode_by_sku[sku] = bc
    except Exception as e:  # noqa: BLE001
        # The spine fallback is best-effort -- a missing `products` collection is
        # not fatal (catalog_variants is the primary source).
        logger.warning("Mongo read(products) fallback failed: %s", e)

    return snap


# ===========================================================================
# CLI
# ===========================================================================

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of the text table.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=25,
        help="Max number of sample missing-SKUs / barcode-mismatches to list "
        "(default: 25). The full lists are always in the --json output.",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("MONGO_DATABASE", "ims_2_0"),
        help="Mongo database name (default: $MONGO_DATABASE or ims_2_0).",
    )
    args = parser.parse_args(argv)

    pg_url = _resolve_pg_url()
    mongo_url = _resolve_mongo_url()

    # Status line -- prints SET/NOT SET only, NEVER the secret value.
    logger.info("=" * 60)
    logger.info("BVI Phase 0 Parity Oracle (READ-ONLY)")
    logger.info(
        "BVI Postgres URL : %s",
        "SET" if pg_url else "NOT SET (BVI_DATABASE_URL / ECOMMERCE_DATABASE_URL)",
    )
    logger.info(
        "IMS Mongo URL    : %s",
        "SET" if mongo_url else "NOT SET (MONGODB_URL / MONGO_URL)",
    )
    logger.info("Mongo database   : %s", args.db)
    logger.info("=" * 60)

    if not pg_url:
        logger.error(
            "FAIL LOUD: no BVI Postgres URL. Set BVI_DATABASE_URL or "
            "ECOMMERCE_DATABASE_URL (inject via `railway run`)."
        )
        return 1
    if not mongo_url:
        logger.error(
            "FAIL LOUD: no IMS Mongo URL. Set MONGODB_URL or MONGO_URL "
            "(inject via `railway run`)."
        )
        return 1

    # --- connect (read-only) ---
    pg_conn = _pg_connect(pg_url)
    if pg_conn is None:
        logger.error("FAIL LOUD: cannot connect to BVI Postgres.")
        return 1
    logger.info("Postgres connected (read-only).")

    mongo_client, mongo_db = _mongo_connect(mongo_url, args.db)
    if mongo_db is None:
        try:
            pg_conn.close()
        except Exception:  # noqa: BLE001
            pass
        logger.error("FAIL LOUD: cannot connect to IMS Mongo.")
        return 1
    logger.info("Mongo connected.")

    # --- collect snapshots (SELECT / find only) ---
    try:
        bvi = collect_bvi_snapshot(pg_conn)
        ims = collect_ims_snapshot(mongo_db)
    finally:
        try:
            pg_conn.close()
        except Exception:  # noqa: BLE001
            pass
        if mongo_client is not None:
            try:
                mongo_client.close()
            except Exception:  # noqa: BLE001
                pass

    # Guard: a -1 count means a read error -- fail loud rather than report parity.
    for label, val in (
        ("BVI Product", bvi.products),
        ("BVI ProductVariant", bvi.variants),
        ("IMS catalog_products", ims.catalog_products),
        ("IMS catalog_variants", ims.catalog_variants),
    ):
        if val < 0:
            logger.error("FAIL LOUD: could not read %s count.", label)
            return 1

    # --- compare (pure) + render ---
    report = build_parity_report(bvi, ims, sample_limit=args.sample)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text_report(report))

    # Exit code: 0 = gate pass, 2 = ran but parity not met.
    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
