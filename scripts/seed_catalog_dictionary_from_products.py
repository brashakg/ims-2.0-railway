#!/usr/bin/env python3
"""
IMS 2.0 - Seed the Catalog Dictionary from existing product attributes
======================================================================
One-off/repeatable runbook script (NOT in CI). For the eyewear categories
SUNGLASS + FRAME it collects every DISTINCT non-empty value the `products`
collection already stores under attributes.<field> for these fields:

    frame_color, temple_color, tint, frame_material, warranty,
    country_of_origin

and MERGES them into the Settings -> Catalog Dictionary storage
(`catalog_field_options`, one doc per (field_id, category) scope --
see backend/api/routers/catalog_field_options.py). Merge semantics:

  * UNION -- existing configured values are ALWAYS preserved; product values
    are only appended. Existing casing wins (case-insensitive de-dupe via the
    backend's own normalize_items helper, first casing kept).
  * The field's "All categories" (global) list, when configured, is folded
    into the new category-scoped list FIRST: at enforcement time a category
    list REPLACES the global one, so seeding a category scope without the
    global values would silently shrink what the owner already allowed.
  * NO deletes, ever. An empty result for a field/category writes nothing.
  * Values longer than the dictionary's MAX_VALUE_LENGTH are skipped (warned);
    lists are capped at MAX_VALUES_PER_FIELD (warned when truncated).

SAFETY CONTRACT
---------------
- --dry-run is the DEFAULT. NOTHING is written without --apply.
- --apply performs idempotent upserts (re-running is a no-op).
- Connection: MONGO_PUBLIC_URL or MONGODB_URI (also accepts MONGODB_URL /
  MONGO_URL, or the MONGO_* component vars Railway injects), or --mongo-uri.
- No emojis (Windows cp1252 safe).

USAGE
-----
Dry run (default -- report only):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/seed_catalog_dictionary_from_products.py

Apply (writes the merged lists):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/seed_catalog_dictionary_from_products.py --apply

Exit codes: 0 = OK / dry-run done; 1 = fatal error.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Reuse the backend's OWN dictionary helpers so seeded values obey the exact
# same normalisation (trim, case-insensitive de-dupe, caps) the Settings
# editor + create-door enforcement use.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

from api.services.catalog_dictionary import (  # noqa: E402
    FIELD_OPTIONS_COLLECTION,
    MAX_VALUE_LENGTH,
    MAX_VALUES_PER_FIELD,
    normalize_items,
)

# The eyewear fields + categories to seed (owner-locked, 2026-07-04 rework).
SEED_FIELDS = (
    "frame_color",
    "temple_color",
    "tint",
    "frame_material",
    "warranty",
    "country_of_origin",
)
# canonical category -> the values products may store on `category` (the spine
# stores the canonical long form; legacy/imported rows may carry the short
# SKU-prefix code).
SEED_CATEGORIES: Dict[str, List[str]] = {
    "SUNGLASS": ["SUNGLASS", "SG", "SUNGLASSES"],
    "FRAME": ["FRAME", "FR", "FRAMES"],
}

UPDATED_BY = "seed_catalog_dictionary_from_products"


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; otherwise assemble one from the
    MONGO_* component vars Railway injects. MONGO_PUBLIC_URL first so the
    runbook works locally via `railway run` (the internal host is only
    resolvable inside Railway's network)."""
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


def collect_product_values(db, field: str, category_forms: List[str]) -> List[str]:
    """Distinct non-empty attributes.<field> values for products whose
    category matches any of `category_forms`. Trimmed; blanks and non-string
    scalars are coerced via str(); overlong values are skipped with a warning."""
    raw = db["products"].distinct(
        f"attributes.{field}", {"category": {"$in": category_forms}}
    )
    out: List[str] = []
    for v in raw:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        if len(s) > MAX_VALUE_LENGTH:
            print(
                f"  [WARN] {field}: skipping over-long value "
                f"({len(s)} chars > {MAX_VALUE_LENGTH}): {s[:40]}..."
            )
            continue
        out.append(s)
    return out


def merge_lists(existing: List[str], global_items: List[str], found: List[str]) -> List[str]:
    """Union with the dictionary's own normaliser: existing scope values first
    (their casing is canonical), then the global list (folded in so a new
    category scope never shrinks the effective allowed set), then the values
    found on products. Capped at MAX_VALUES_PER_FIELD (never raises)."""
    combined = list(existing) + list(global_items) + list(found)
    # normalize_items raises when the list exceeds the cap -- pre-truncate on
    # the case-insensitive de-duped sequence so seeding never crashes.
    deduped: List[str] = []
    seen = set()
    for s in combined:
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    if len(deduped) > MAX_VALUES_PER_FIELD:
        print(
            f"  [WARN] merged list exceeds {MAX_VALUES_PER_FIELD} values; "
            f"truncating {len(deduped) - MAX_VALUES_PER_FIELD} (existing values kept first)."
        )
        deduped = deduped[:MAX_VALUES_PER_FIELD]
    return normalize_items(deduped)


def run(*, mongo_uri: Optional[str], db_name: str, apply: bool) -> int:
    if not mongo_uri:
        print(
            "[ERROR] No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, "
            "pass --mongo-uri, or run via `railway run` so the MONGO_* vars "
            "are injected."
        )
        return 1
    try:
        from pymongo import MongoClient
    except ImportError:
        print("[ERROR] pymongo not installed; run `pip install pymongo`.")
        return 1

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    coll = db[FIELD_OPTIONS_COLLECTION]
    mode = "APPLY" if apply else "DRY-RUN (nothing will be written; use --apply)"
    print(f"[INFO] seed_catalog_dictionary_from_products -- {mode}")
    print(f"[INFO] db={db_name}  fields={list(SEED_FIELDS)}  categories={list(SEED_CATEGORIES)}")

    writes = 0
    for canonical, category_forms in SEED_CATEGORIES.items():
        for field in SEED_FIELDS:
            found = collect_product_values(db, field, category_forms)

            scope_doc = coll.find_one({"field_id": field, "category": canonical}) or {}
            existing = [str(i) for i in (scope_doc.get("items") or []) if str(i or "").strip()]
            # "All categories" doc: category null OR absent (legacy shape).
            global_doc = (
                coll.find_one({"field_id": field, "category": None})
                or coll.find_one({"field_id": field, "category": {"$exists": False}})
                or {}
            )
            global_items = [str(i) for i in (global_doc.get("items") or []) if str(i or "").strip()]

            merged = merge_lists(existing, global_items, found)
            added = len(merged) - len(
                merge_lists(existing, global_items, [])
            )
            print(
                f"[{canonical}] {field}: products={len(found)} existing={len(existing)} "
                f"global={len(global_items)} -> merged={len(merged)} (+{added} new)"
            )
            if not merged:
                continue  # nothing to write; never write an empty list
            if merged == existing:
                print(f"  [SKIP] no change for {canonical}/{field}")
                continue
            if apply:
                coll.update_one(
                    {"field_id": field, "category": canonical},
                    {
                        "$set": {
                            "field_id": field,
                            "category": canonical,
                            "items": merged,
                            "updated_at": datetime.now(timezone.utc),
                            "updated_by": UPDATED_BY,
                        }
                    },
                    upsert=True,
                )
                writes += 1
                print(f"  [WRITE] upserted {canonical}/{field} ({len(merged)} values)")

    if apply:
        print(f"[DONE] {writes} scope doc(s) upserted.")
    else:
        print("[DONE] dry-run complete. Re-run with --apply to write.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed Catalog Dictionary lists from existing product attributes (union, no deletes)."
    )
    parser.add_argument("--mongo-uri", default=None, help="Explicit Mongo URI (else env)")
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Report only (DEFAULT)")
    group.add_argument("--apply", action="store_true", help="Write the merged lists")
    args = parser.parse_args()
    return run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        apply=bool(args.apply),
    )


if __name__ == "__main__":
    sys.exit(main())
