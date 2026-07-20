#!/usr/bin/env python3
"""
IMS 2.0 - Backfill product display names + online-catalog DRAFT docs
====================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
Products created via the Add-Product flow historically landed in the `products`
spine with NO top-level `name` -- so they showed blank on bills, POS lines and
the online catalog even though `attributes` held rich specs (brand, model,
shape, colour, gender). They also never reached the online catalog
(`catalog_products`): each spine carries a `pim_product_id`, but the ONLY writer
of that catalog_products doc was the off-by-default mirror, so the id pointed at
a doc that was never created (a dangling link).

This script sweeps the existing IMS-origin spine products that still have a
blank name and:
  1. mints a deterministic, SEO-shaped `name` (product_naming.build_product_name)
     + a SEO description placeholder, and
  2. stages / repairs their catalog_products DRAFT doc (ecom.status=DRAFT,
     ecom.handle, ecom.seo.{title,description,tags}) so they CAN be pushed
     online later.

SCOPE
-----
Only IMS-origin products: `bvi_product_id` absent (BVI/Shopify-lineage rows
already carry their own names + PIM docs and must not be touched) AND the
top-level `name` blank/missing. Idempotent: a non-blank `name` is NEVER
overwritten; the catalog_products doc is upserted on its stable `id`.

Nothing external is written -- catalog_products is the INTERNAL PIM shadow and
every staged doc is ecom.status=DRAFT (never live). No Shopify/Postgres call.

WHAT IT TOUCHES
---------------
  - `products`          : sets top-level `name` (+ `seo_description`) on rows
                          with a blank name.
  - `catalog_products`  : upserts the DRAFT PIM doc keyed by `id` =
                          products.pim_product_id (mints one if the spine has
                          no pim_product_id yet).

USAGE
-----
Dry-run (DEFAULT - prints a before->after table, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_product_names.py

Apply:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/backfill_product_names.py --apply

Locally with an explicit URI:
    python scripts/backfill_product_names.py --mongo-uri mongodb://... --apply

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL, else the MONGO_* component vars Railway injects.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

# Make the backend package importable so we reuse the SAME pure builders the
# live create door uses (no logic drift between runtime + backfill).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

from api.services.product_naming import (  # noqa: E402
    build_handle,
    build_product_name,
    build_seo_description,
    build_seo_title,
)

# A blank-name spine row is one where `name` is missing, None, or whitespace.
_BLANK_NAME_QUERY: Dict[str, Any] = {
    "bvi_product_id": {"$exists": False},
    "$or": [
        {"name": {"$exists": False}},
        {"name": None},
        {"name": ""},
        {"name": {"$regex": r"^\s*$"}},
    ],
}


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; otherwise assemble one from the MONGO_*
    component vars Railway injects. MONGO_PUBLIC_URL is checked first so this
    runbook works locally via `railway run -s MongoDB ...`."""
    uri = (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )
    if uri:
        return uri
    host = os.getenv("MONGO_HOST")
    if not host:
        return None
    user = os.getenv("MONGO_USERNAME") or ""
    pw = os.getenv("MONGO_PASSWORD") or ""
    port = os.getenv("MONGO_PORT", "27017")
    auth_source = os.getenv("MONGO_AUTH_SOURCE", "admin")
    cred = f"{user}:{pw}@" if user and pw else ""
    opts = f"?authSource={auth_source}"
    if (os.getenv("MONGO_SSL", "") or "").lower() in ("true", "1", "yes"):
        opts += "&tls=true"
    return f"mongodb://{cred}{host}:{port}/{opts}"


def _build_pim_draft(spine: Dict[str, Any], name: str) -> Dict[str, Any]:
    """The catalog_products DRAFT PIM doc for a spine row. Mirrors
    product_master._build_pim_doc (ecom.status=DRAFT, handle + seo) so a
    backfilled product is push-able exactly like a freshly created one."""
    attrs = dict(spine.get("attributes") or {})
    seo_title = build_seo_title(spine) or name
    handle = build_handle(spine)
    seo_description = build_seo_description(spine)
    return {
        "id": spine.get("pim_product_id"),
        "parent_sku": spine.get("sku"),
        "category": spine.get("category"),
        "sku_prefix": spine.get("sku_prefix"),
        "brand": spine.get("brand"),
        "model": spine.get("model"),
        "mrp": spine.get("mrp"),
        "offer_price": spine.get("offer_price"),
        "hsn_code": spine.get("hsn_code"),
        "gst_rate": spine.get("gst_rate"),
        "name": name or None,
        "title": name or None,
        "status": "DRAFT",
        "ecom": {
            "status": "DRAFT",
            "handle": handle or None,
            "seo": {
                "title": seo_title or None,
                "description": seo_description or None,
                "tags": list(spine.get("tags") or []),
            },
            "category_specific": attrs,
        },
        "attributes": attrs,
    }


def run(*, mongo_uri: Optional[str], db_name: str, apply: bool) -> Dict[str, Any]:
    if not mongo_uri:
        raise SystemExit(
            "No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass "
            "--mongo-uri, or run via `railway run` so the MONGO_* component "
            "vars are injected."
        )
    try:
        from pymongo import MongoClient
    except ImportError:
        raise SystemExit("pymongo not installed; run `pip install pymongo`.")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    products = db["products"]
    catalog = db["catalog_products"]

    total = products.count_documents({})
    candidates: List[Dict[str, Any]] = list(products.find(_BLANK_NAME_QUERY))

    rows: List[Dict[str, str]] = []
    named = 0
    unnamed = 0
    catalog_staged = 0
    mode = "APPLY" if apply else "DRY-RUN"

    for spine in candidates:
        sku = str(spine.get("sku") or "")
        before = spine.get("name") or ""
        minted = build_product_name(spine)
        if not minted:
            # Nothing to derive a name from (no brand/model/name). Skip -- never
            # stamp a blank.
            unnamed += 1
            rows.append({"sku": sku, "before": "(blank)", "after": "(SKIPPED - no data)"})
            continue

        # Ensure the spine has a pim_product_id so the catalog doc has a stable
        # link key (older rows created before the id was assigned may lack it).
        pim_id = spine.get("pim_product_id")
        set_fields: Dict[str, Any] = {"name": minted}
        seo_desc = build_seo_description(spine)
        if seo_desc:
            set_fields["seo_description"] = seo_desc
        if not pim_id:
            pim_id = str(uuid.uuid4())
            spine["pim_product_id"] = pim_id
            set_fields["pim_product_id"] = pim_id

        pim_doc = _build_pim_draft(spine, minted)

        if apply:
            products.update_one({"_id": spine["_id"]}, {"$set": set_fields})
            catalog.update_one({"id": pim_id}, {"$set": pim_doc}, upsert=True)
        named += 1
        catalog_staged += 1
        rows.append({"sku": sku, "before": before or "(blank)", "after": minted})

    # --- before -> after table ------------------------------------------------
    print(f"[{mode}] products total={total}  blank-name IMS-origin candidates={len(candidates)}")
    print(f"[{mode}] will name={named}  skipped(no data)={unnamed}  catalog DRAFT staged={catalog_staged}")
    if rows:
        w_sku = max(3, min(28, max(len(r["sku"]) for r in rows)))
        w_before = max(6, min(24, max(len(r["before"]) for r in rows)))
        print()
        print(f"  {'SKU':<{w_sku}}  {'BEFORE':<{w_before}}  ->  AFTER")
        print(f"  {'-' * w_sku}  {'-' * w_before}  --  {'-' * 40}")
        for r in rows:
            print(f"  {r['sku'][:w_sku]:<{w_sku}}  {r['before'][:w_before]:<{w_before}}  ->  {r['after']}")
        print()
    if not apply and named:
        print(f"[DRY-RUN] re-run with --apply to name {named} product(s) + stage "
              f"{catalog_staged} catalog DRAFT doc(s).")
    return {
        "products_total": total,
        "candidates": len(candidates),
        "named": named,
        "skipped_no_data": unnamed,
        "catalog_staged": catalog_staged,
        "applied": apply,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Mint SEO display names + stage online-catalog DRAFT docs for "
            "IMS-origin products with a blank name. Idempotent; dry-run by "
            "default; never overwrites a non-blank name."
        )
    )
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="Mongo URI; falls back to MONGO_PUBLIC_URL / MONGODB_URI / "
             "MONGODB_URL / MONGO_URL then MONGO_* components.",
    )
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()
    run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        apply=args.apply,
    )


if __name__ == "__main__":
    sys.exit(main())
