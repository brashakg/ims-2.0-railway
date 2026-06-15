"""
IMS 2.0 - Hub Phase 1 data-hygiene: identity backfill + duplicate prep
======================================================================
The Phase-1 duplicate guard refuses a new product that collides with an existing
one by SKU / brand+model+colour identity / barcode, and a REPORT-ONLY
`identity_key` index backs it. For the guard to catch collisions against the
EXISTING ~10,800 rows -- and for the eventual UNIQUE index flip (Phase 6) -- the
live data needs four one-time fixes:

  1. BACKFILL identity_key onto every row missing it (derived from brand+model+
     colour), so the guard + index see legacy rows. Safe + additive.
  2. UNSET empty-string ("") barcodes. A unique+sparse barcode index treats "" as
     a real value, so many blank barcodes collide; $unset makes the sparse index
     skip them. Safe.
  3. RE-SKU duplicate SKUs (owner decision 2026-06-12: keep both rows as distinct
     products, suffix the newer). Unblocks the unique sku index with zero merge.
  4. ASSIGN a fresh product_id to any row missing one (the product_id unique index
     rejects nulls); report them.

SAFETY
------
- DRY-RUN BY DEFAULT (re-SKU + identity writes are real mutations). Prints exactly
  what WOULD change; pass --apply to write.
- IDEMPOTENT. Re-runs match nothing once applied.
- AUDIT-LOGGED. One summary row per fix to `audit_log` (kind=dedupe_prep_phase1).
- FAIL-LOUD on no DB. Exits non-zero rather than silently doing nothing.

USAGE
-----
  # dry run (default):
  railway run .venv\\Scripts\\python.exe backend/scripts/backfill_dedupe_prep.py
  # apply:
  railway run .venv\\Scripts\\python.exe backend/scripts/backfill_dedupe_prep.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

AUDIT_KIND = "dedupe_prep_phase1"


def _identity_of(doc: Dict[str, Any]):
    """Derive identity_key from a spine doc's identity (top-level or attributes),
    reusing the canonical service rule so the script and the live guard agree."""
    from api.services.product_master import compute_identity_key

    attrs = doc.get("attributes") or {}
    brand = doc.get("brand") or attrs.get("brand_name") or attrs.get("brand")
    model = (
        doc.get("model")
        or attrs.get("model_no")
        or attrs.get("model_name")
        or attrs.get("model")
    )
    colour = (
        doc.get("color")
        or attrs.get("colour_code")
        or attrs.get("colour_name")
        or attrs.get("color")
    )
    return compute_identity_key(brand, model, colour)


def _pid(doc: Dict[str, Any]) -> str:
    return str(doc.get("product_id") or doc.get("_id") or "")


def _sort_key(doc: Dict[str, Any]):
    """Deterministic order so the SAME row keeps the bare SKU on every run/env:
    oldest created_at first, then product_id. Unordered find({}) is storage-order
    dependent, which would make 'which dup keeps the legacy SKU' non-deterministic."""
    return (
        str(doc.get("created_at") or ""),
        str(doc.get("product_id") or doc.get("_id") or ""),
    )


def run_dedupe(
    products, *, apply: bool, catalog_products=None, catalog_variants=None
) -> Dict[str, Any]:
    """Pure-ish core (takes the products collection). Returns counts + the
    old->new SKU rename map. Reads every product once (DETERMINISTICALLY ordered),
    computes the four fixes, and (when apply) writes them per-row keyed on
    product_id/_id. A re-SKU also renames the matching catalog_products /
    catalog_variants mirror rows (when those collections are supplied) so the
    SKU-string join is not orphaned; the suffix is collision-checked against all
    existing + freshly-minted SKUs."""
    rows = sorted(list(products.find({})), key=_sort_key)
    stats: Dict[str, Any] = {
        "scanned": len(rows),
        "identity_backfilled": 0,
        "barcode_unset": 0,
        "resku": 0,
        "pid_assigned": 0,
        "resku_map": {},
    }
    # All SKUs that already exist -- the re-SKU suffix must avoid every one of them
    # (plus any it mints this run) so it never re-creates the exact dup we are clearing.
    all_skus = {doc.get("sku") for doc in rows if doc.get("sku")}
    seen_skus: Dict[str, int] = {}

    def _mint_unique(base: str) -> str:
        n = 2
        candidate = f"{base}-DUP{n}"
        while candidate in all_skus:
            n += 1
            candidate = f"{base}-DUP{n}"
        all_skus.add(candidate)
        return candidate

    for doc in rows:
        match = (
            {"product_id": doc["product_id"]}
            if doc.get("product_id")
            else {"_id": doc.get("_id")}
        )
        set_fields: Dict[str, Any] = {}
        unset_fields: Dict[str, Any] = {}

        # (4) missing product_id -> assign a fresh uuid (report + fix).
        if not doc.get("product_id"):
            import uuid as _uuid

            stats["pid_assigned"] += 1
            if apply:
                set_fields["product_id"] = str(_uuid.uuid4())

        # (1) identity_key backfill (only when absent + derivable).
        if not doc.get("identity_key"):
            ident = _identity_of(doc)
            if ident:
                stats["identity_backfilled"] += 1
                if apply:
                    set_fields["identity_key"] = ident

        # (2) empty-string barcode -> unset (sparse-index hygiene).
        if doc.get("barcode") == "":
            stats["barcode_unset"] += 1
            if apply:
                unset_fields["barcode"] = ""

        # (3) duplicate SKU -> re-SKU the SECOND+ occurrence (collision-checked
        # suffix), and rename the mirror rows so the SKU join is not orphaned.
        sku = doc.get("sku")
        if sku:
            seen_skus[sku] = seen_skus.get(sku, 0) + 1
            if seen_skus[sku] > 1:
                new_sku = _mint_unique(sku)
                stats["resku"] += 1
                stats["resku_map"][f"{sku}#{seen_skus[sku]}"] = new_sku
                if apply:
                    set_fields["sku"] = new_sku
                    for mirror in (catalog_products, catalog_variants):
                        if mirror is not None:
                            try:
                                mirror.update_one(
                                    {"sku": sku, "product_id": doc.get("product_id")},
                                    {"$set": {"sku": new_sku}},
                                )
                            except Exception:  # noqa: BLE001
                                pass

        if apply and (set_fields or unset_fields):
            update: Dict[str, Any] = {}
            if set_fields:
                update["$set"] = set_fields
            if unset_fields:
                update["$unset"] = unset_fields
            products.update_one(match, update)

    return stats


def _connect():
    from database.connection import init_db, get_db, DatabaseConfig

    mongo_url = os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
    config = (
        DatabaseConfig.from_uri(mongo_url, database="ims_2_0")
        if mongo_url
        else DatabaseConfig.from_env()
    )
    return get_db() if init_db(config) else None


def run(apply: bool) -> int:
    db = _connect()
    if db is None or not db.is_connected:
        print(
            "[ERROR] Could not connect to MongoDB. Set MONGODB_URL / MONGO_URL "
            "(or run via `railway run`). Nothing changed."
        )
        return 2
    products = db.get_collection("products")
    if products is None:
        print("[ERROR] products collection unavailable. Nothing changed.")
        return 2

    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 70)
    print(f"Hub Phase 1 dedupe-prep  [{mode}]")
    print("=" * 70)
    stats = run_dedupe(
        products,
        apply=apply,
        catalog_products=db.get_collection("catalog_products"),
        catalog_variants=db.get_collection("catalog_variants"),
    )
    for k in (
        "scanned",
        "identity_backfilled",
        "barcode_unset",
        "resku",
        "pid_assigned",
    ):
        print(f"  {k}: {stats[k]}")
    if stats.get("resku_map"):
        print("  re-SKU rename map (occurrence -> new sku):")
        for old, new in stats["resku_map"].items():
            print(f"    {old} -> {new}")
    if not apply:
        print("\n[DRY-RUN] No changes written. Re-run with --apply to commit.")
        return 0

    audit = db.get_collection("audit_log")
    if audit is not None:
        try:
            audit.insert_one(
                {
                    "kind": AUDIT_KIND,
                    "collection": "products",
                    "stats": stats,
                    "reason": "Hub Phase 1: identity backfill + duplicate prep "
                    "(re-SKU dup SKUs, unset empty barcodes, assign null product_ids).",
                    "created_at": datetime.utcnow(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] audit_log summary write failed: {exc}")
    print("\n[APPLY] Done. Re-running is a no-op (idempotent).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hub Phase 1 dedupe-prep (identity backfill + duplicate fixes)."
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write changes (default dry-run)."
    )
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
