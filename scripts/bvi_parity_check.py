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

The comparison core now lives in backend/api/services/bvi_parity.py (so the
NIGHTLY parity monitor -- SENTINEL's ~02:30 IST tick -- and this manual CLI
share ONE implementation). This script imports that service and re-exports the
same names it always exposed (BviSnapshot, ImsSnapshot, build_parity_report,
render_text_report, is_durable_image_url, collect_bvi_snapshot,
collect_ims_snapshot), so both the CLI contract and the unit-test imports
(backend/tests/test_bvi_parity_check.py) are unchanged.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import List, Optional

# Make the backend package importable when this script is run directly
# (python scripts/bvi_parity_check.py) -- the comparison core lives in
# backend/api/services/bvi_parity.py.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Re-exported names (some unused here) keep this module's historical import
# surface intact for tests + any runbook one-liners.
from api.services.bvi_parity import (  # noqa: E402,F401
    BviSnapshot,
    ImsSnapshot,
    build_parity_report,
    collect_bvi_snapshot,
    collect_ims_snapshot,
    is_durable_image_url,
    render_text_report,
    _norm_barcode,
    _norm_sku,
    _pg_connect,
    _pg_count,
    _pg_fetchall,
    _resolve_pg_url,
)

__all__ = [
    "BviSnapshot",
    "ImsSnapshot",
    "build_parity_report",
    "collect_bvi_snapshot",
    "collect_ims_snapshot",
    "is_durable_image_url",
    "render_text_report",
    "main",
]

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bvi_parity_check")


def _resolve_mongo_url() -> Optional[str]:
    return os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")


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
