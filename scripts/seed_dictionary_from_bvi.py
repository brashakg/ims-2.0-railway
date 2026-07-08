#!/usr/bin/env python3
"""
IMS 2.0 - Seed Brand Master + Catalog Dictionary from the BVI import
====================================================================
One-off/repeatable runbook script (NOT in CI). Companion to the Catalog
Manager review queue: the promote gate validates every imported product
through the canonical door, which enforces

  * attributes.brand_name against the ACTIVE Brand Master (`brand_masters`)
    for the product's category -- enforced whenever the master has at least
    one applicable brand, so on prod (where the owner already seeded brands)
    every BVI brand missing from the master would 422 the approval;
  * attributes.<field> against Settings -> Catalog Dictionary
    (`catalog_field_options`) whenever a list is configured for that field.

This script therefore harvests the 4,393 imported docs (catalog_products,
source="bvi_import") and:

  1. BRAND MASTER: for every distinct attributes.brand_name it UNIONS the
     observed category prefixes onto an existing (case-insensitive) brand's
     `categories`, or INSERTS a new brand doc (tier defaults to MASS -- the
     most conservative discount band; the owner can retune in Settings).
     Existing brands' name/tier/flags are NEVER modified.
  2. SUB-BRANDS: distinct attributes.sub_brand per brand are inserted into
     `subbrand_masters` (skipping ones the brand already has).
  3. DICTIONARY: distinct attributes.shape / attributes.frame_material values
     are UNION-merged into the per-category catalog_field_options lists using
     the backend's own normaliser (existing values + casing always win).
     Fields with NO configured list and NO harvested values are left alone.

SAFETY CONTRACT
---------------
- --dry-run is the DEFAULT. NOTHING is written without --apply.
- --apply additionally asks for interactive confirmation (type "yes");
  --yes skips the prompt for non-interactive runs.
- Merge-only: no deletes, no overwrites of existing values/casings/tiers.
- Connection: MONGO_PUBLIC_URL or MONGODB_URI (also MONGODB_URL / MONGO_URL,
  or the MONGO_* component vars Railway injects), or --mongo-uri.
- No emojis (Windows cp1252 safe).

USAGE
-----
Dry run (default -- report only):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/seed_dictionary_from_bvi.py

Apply (writes; asks for confirmation):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/seed_dictionary_from_bvi.py --apply

Exit codes: 0 = OK / dry-run done; 1 = fatal error; 2 = confirmation refused.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

from api.services.catalog_dictionary import (  # noqa: E402
    BRAND_COLLECTION,
    FIELD_OPTIONS_COLLECTION,
    MAX_VALUE_LENGTH,
    MAX_VALUES_PER_FIELD,
    SUBBRAND_COLLECTION,
    normalize_items,
)
from api.services.product_master import category_spec, resolve_category  # noqa: E402

SOURCE_FILTER = {"source": "bvi_import"}
DICTIONARY_FIELDS = ("shape", "frame_material")
DEFAULT_TIER = "MASS"  # most conservative discount band; owner retunes later
UPDATED_BY = "seed_dictionary_from_bvi"


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; otherwise assemble one from the
    MONGO_* component vars Railway injects."""
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


def _now():
    return datetime.now(timezone.utc)


def _brand_code(name: str, taken: Set[str]) -> str:
    """A unique short code for a new brand doc (brand_masters.code is treated
    as unique by the admin create door)."""
    base = "".join(ch for ch in name.upper() if ch.isalnum())[:12] or "BRAND"
    code = base
    n = 1
    while code.casefold() in taken:
        n += 1
        code = f"{base}{n}"
    taken.add(code.casefold())
    return code


def harvest(db) -> Dict:
    """Read the imported docs once and build: brand -> {prefixes, subbrands},
    and (canonical category, field) -> distinct values."""
    brands: Dict[str, Dict] = {}
    dict_values: Dict[tuple, Set[str]] = {}
    scanned = 0
    for doc in db["catalog_products"].find(SOURCE_FILTER):
        scanned += 1
        attrs = doc.get("attributes") or {}
        canonical = resolve_category(doc.get("category")) or str(
            doc.get("category") or ""
        ).strip().upper()
        spec = category_spec(canonical)
        prefix = spec.prefix if spec is not None else None

        brand = str(attrs.get("brand_name") or "").strip()
        if brand and len(brand) <= 80:
            entry = brands.setdefault(
                brand.casefold(), {"name": brand, "prefixes": set(), "subbrands": set()}
            )
            if prefix:
                entry["prefixes"].add(prefix)
            sub = str(attrs.get("sub_brand") or attrs.get("subbrand") or "").strip()
            if sub and len(sub) <= 80:
                entry["subbrands"].add(sub)

        if canonical:
            for field in DICTIONARY_FIELDS:
                val = str(attrs.get(field) or "").strip()
                if not val:
                    continue
                if len(val) > MAX_VALUE_LENGTH:
                    print(
                        f"  [WARN] {field}: skipping over-long value "
                        f"({len(val)} chars): {val[:40]}..."
                    )
                    continue
                dict_values.setdefault((canonical, field), set()).add(val)

    print(f"[INFO] scanned {scanned} imported doc(s) (source=bvi_import)")
    return {"brands": brands, "dict_values": dict_values}


def seed_brands(db, harvested: Dict, apply: bool) -> None:
    coll = db[BRAND_COLLECTION]
    existing_by_name: Dict[str, Dict] = {}
    taken_codes: Set[str] = set()
    for doc in coll.find({}):
        name = str(doc.get("name") or "").strip()
        if name:
            existing_by_name[name.casefold()] = doc
        code = str(doc.get("code") or "").strip()
        if code:
            taken_codes.add(code.casefold())

    created = updated = 0
    for key, entry in sorted(harvested["brands"].items()):
        prefixes = sorted(entry["prefixes"])
        existing = existing_by_name.get(key)
        if existing is None:
            doc = {
                "brand_id": uuid.uuid4().hex[:12],
                "_id": None,  # set below
                "name": entry["name"],
                "code": _brand_code(entry["name"], taken_codes),
                "categories": prefixes,
                "tier": DEFAULT_TIER,
                "description": "Seeded from the BVI import",
                "status": "ACTIVE",
                "sync_to_shopify_default": False,
                "is_active": True,
                "created_at": _now(),
                "updated_at": _now(),
                "created_by": UPDATED_BY,
            }
            doc["_id"] = doc["brand_id"]
            print(f"[BRAND +] {entry['name']}  categories={prefixes} tier={DEFAULT_TIER}")
            if apply:
                coll.insert_one(doc)
            existing_by_name[key] = doc
            created += 1
        else:
            # UNION missing category prefixes only; never touch anything else.
            # A brand whose categories list is EMPTY applies to ALL categories
            # already -- leave it empty (narrowing it would be a behaviour
            # change, not a merge).
            current = [str(c) for c in (existing.get("categories") or [])]
            if not current:
                continue
            missing = [p for p in prefixes if p not in current]
            if not missing:
                continue
            print(f"[BRAND ~] {existing.get('name')}  + categories {missing}")
            if apply:
                coll.update_one(
                    {"brand_id": existing.get("brand_id")},
                    {
                        "$set": {
                            "categories": current + missing,
                            "updated_at": _now(),
                            "updated_by": UPDATED_BY,
                        }
                    },
                )
            updated += 1

    # Sub-brands: insert the missing ones per brand.
    subs_coll = db[SUBBRAND_COLLECTION]
    sub_created = 0
    for key, entry in sorted(harvested["brands"].items()):
        if not entry["subbrands"]:
            continue
        brand_doc = existing_by_name.get(key)
        brand_id = (brand_doc or {}).get("brand_id")
        if not brand_id:
            continue
        have = {
            str(s.get("name") or "").strip().casefold()
            for s in subs_coll.find({"brand_id": brand_id})
        }
        for sub in sorted(entry["subbrands"]):
            if sub.casefold() in have:
                continue
            print(f"[SUBBRAND +] {entry['name']} -> {sub}")
            if apply:
                sub_doc = {
                    "subbrand_id": uuid.uuid4().hex[:12],
                    "brand_id": brand_id,
                    "name": sub,
                    "code": "".join(ch for ch in sub.upper() if ch.isalnum())[:24]
                    or "SUB",
                    "created_at": _now(),
                    "updated_at": _now(),
                    "created_by": UPDATED_BY,
                }
                sub_doc["_id"] = sub_doc["subbrand_id"]
                subs_coll.insert_one(sub_doc)
            sub_created += 1

    print(
        f"[BRANDS] {created} new brand(s), {updated} category union(s), "
        f"{sub_created} new sub-brand(s)"
        + ("" if apply else "  [dry-run: nothing written]")
    )


def merge_lists(existing: List[str], global_items: List[str], found: List[str]) -> List[str]:
    """Union preserving existing casing first (same rules as the sibling
    seed_catalog_dictionary_from_products.py script)."""
    combined = list(existing) + list(global_items) + list(found)
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
            f"truncating {len(deduped) - MAX_VALUES_PER_FIELD}."
        )
        deduped = deduped[:MAX_VALUES_PER_FIELD]
    return normalize_items(deduped)


def seed_dictionary(db, harvested: Dict, apply: bool) -> None:
    coll = db[FIELD_OPTIONS_COLLECTION]
    writes = 0
    for (canonical, field), values in sorted(harvested["dict_values"].items()):
        found = sorted(values)
        scope_doc = coll.find_one({"field_id": field, "category": canonical}) or {}
        existing = [
            str(i) for i in (scope_doc.get("items") or []) if str(i or "").strip()
        ]
        global_doc = (
            coll.find_one({"field_id": field, "category": None})
            or coll.find_one({"field_id": field, "category": {"$exists": False}})
            or {}
        )
        global_items = [
            str(i) for i in (global_doc.get("items") or []) if str(i or "").strip()
        ]
        # Seed a category list ONLY when the owner already configured this
        # field somewhere (category or global): an unconfigured field is
        # free-form (enforcement skips it), and configuring it implicitly
        # would TIGHTEN validation behind the owner's back.
        if not existing and not global_items:
            print(
                f"[{canonical}] {field}: unconfigured (free-form) -- "
                f"skipping {len(found)} harvested value(s)"
            )
            continue
        merged = merge_lists(existing, global_items, found)
        if not merged or merged == existing:
            print(f"[{canonical}] {field}: no change ({len(existing)} values)")
            continue
        print(
            f"[{canonical}] {field}: bvi={len(found)} existing={len(existing)} "
            f"global={len(global_items)} -> merged={len(merged)}"
        )
        if apply:
            coll.update_one(
                {"field_id": field, "category": canonical},
                {
                    "$set": {
                        "field_id": field,
                        "category": canonical,
                        "items": merged,
                        "updated_at": _now(),
                        "updated_by": UPDATED_BY,
                    }
                },
                upsert=True,
            )
            writes += 1
    print(
        f"[DICTIONARY] {writes} scope doc(s) upserted"
        + ("" if apply else "  [dry-run: nothing written]")
    )


def run(*, mongo_uri: Optional[str], db_name: str, apply: bool, assume_yes: bool) -> int:
    if not mongo_uri:
        print(
            "[ERROR] No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, "
            "pass --mongo-uri, or run via `railway run`."
        )
        return 1
    try:
        from pymongo import MongoClient
    except ImportError:
        print("[ERROR] pymongo not installed; run `pip install pymongo`.")
        return 1

    if apply and not assume_yes:
        answer = input(
            "This will WRITE to brand_masters / subbrand_masters / "
            "catalog_field_options. Type 'yes' to continue: "
        ).strip()
        if answer.lower() != "yes":
            print("[ABORT] confirmation refused; nothing written.")
            return 2

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    mode = "APPLY" if apply else "DRY-RUN (nothing will be written; use --apply)"
    print(f"[INFO] seed_dictionary_from_bvi -- {mode}")
    print(f"[INFO] db={db_name} dictionary fields={list(DICTIONARY_FIELDS)}")

    harvested = harvest(db)
    if not harvested["brands"] and not harvested["dict_values"]:
        print("[DONE] nothing harvested (no bvi_import docs?).")
        return 0
    seed_brands(db, harvested, apply)
    seed_dictionary(db, harvested, apply)
    print("[DONE]" + ("" if apply else " dry-run complete. Re-run with --apply to write."))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed Brand Master + Catalog Dictionary from the BVI import "
            "(merge-only, no deletes). Dry-run by default."
        )
    )
    parser.add_argument("--mongo-uri", default=None, help="Explicit Mongo URI (else env)")
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument(
        "--yes", action="store_true", help="Skip the --apply confirmation prompt"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Report only (DEFAULT)")
    group.add_argument("--apply", action="store_true", help="Write the merged data")
    args = parser.parse_args()
    return run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        apply=bool(args.apply),
        assume_yes=bool(args.yes),
    )


if __name__ == "__main__":
    sys.exit(main())
