"""DQ-CATEGORY-SPLIT backfill: normalize non-canonical category names to canonical enum.

BUG
---
Products are stored with non-canonical category names (FRAMES, CONTACT_LENSES,
RX_LENSES, WRIST_WATCHES, SMARTWATCHES, WALL_CLOCKS, COLOUR_CONTACTS, etc.)
instead of the canonical PRODUCT_SCHEMA enum values (FRAME, CONTACT_LENS,
OPTICAL_LENS, WATCH, SMARTWATCH, WALL_CLOCK, COLORED_CONTACT_LENS, etc.).

This causes:
  1. Analytics queries to split results (e.g. 100+ FRAME vs 10 FRAMES products).
  2. Case-sensitive reports to list two variants of the same category.
  3. Potential billing bugs if code checks category directly (GST POS queries
     already use _normalize_category so billing is unaffected, but the catalog
     master is inconsistent with the schema).

FIX
---
For every product with a non-canonical category alias, normalize it to the
canonical form using the GST_CATEGORY_TABLE mapping. Examples:
  FRAMES -> FRAME
  CONTACT_LENSES -> CONTACT_LENS
  RX_LENSES -> OPTICAL_LENS
  WRIST_WATCHES -> WATCH
  SMARTWATCHES -> SMARTWATCH
  WALL_CLOCKS -> WALL_CLOCK
  COLOUR_CONTACTS -> COLORED_CONTACT_LENS

GST rates and HSN codes are NOT changed (both canonical and alias have identical
rates/HSNs per gst_rates.GST_CATEGORY_TABLE). Audit-logged for full reversibility.

IDEMPOTENT + DRY-RUN BY DEFAULT
--------------------------------
- Pass --apply to write changes; without it, only prints what WOULD change.
- Rerunning --apply after a successful run is a no-op (0 updates).
- Every change is audit-logged to the audit_log collection (kind="category_split_fix_2026_06_06").

USAGE
-----
  # Dry run (default - shows count + samples, writes nothing):
  railway run .venv\\Scripts\\python.exe backend/scripts/normalize_category_split.py

  # Apply for real:
  railway run .venv\\Scripts\\python.exe backend/scripts/normalize_category_split.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

# Make the backend package importable whether run from repo root or backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.services.gst_rates import GST_CATEGORY_TABLE  # noqa: E402

# Canonical product categories per PRODUCT_SCHEMA enum.
CANONICAL_CATEGORIES = {
    "FRAME",
    "OPTICAL_LENS",
    "READING_GLASSES",
    "CONTACT_LENS",
    "COLORED_CONTACT_LENS",
    "SUNGLASS",
    "WATCH",
    "SMARTWATCH",
    "SMARTGLASSES",
    "WALL_CLOCK",
    "ACCESSORIES",
    "SERVICES",
    "HEARING_AID",  # not in PRODUCT_SCHEMA but in table for UI/edge cases
}

# Build alias -> canonical mapping from gst_rates.GST_CATEGORY_TABLE.
# Group by (hsn, rate) so we can find the canonical form for each group.
ALIAS_MAPPING = {}
for cat, (hsn, rate) in GST_CATEGORY_TABLE.items():
    if cat not in CANONICAL_CATEGORIES:
        # This is an alias; find its canonical form.
        for canonical, (c_hsn, c_rate) in GST_CATEGORY_TABLE.items():
            if canonical in CANONICAL_CATEGORIES and c_hsn == hsn and c_rate == rate:
                ALIAS_MAPPING[cat] = canonical
                break

# Explicit overrides where multiple canonical categories share the same
# (hsn, rate) so the (hsn, rate) inference above is ambiguous -- notably the
# contact-lens family: CONTACT_LENS and COLORED_CONTACT_LENS are BOTH 900130 @ 5%,
# so the loop could map a colour-contact alias to the plain CONTACT_LENS canonical.
EXPLICIT_ALIASES = {
    "COLOUR_CONTACTS": "COLORED_CONTACT_LENS",
    "COLORED_CONTACT_LENSES": "COLORED_CONTACT_LENS",
    "CONTACT_LENSES": "CONTACT_LENS",
}
ALIAS_MAPPING.update(EXPLICIT_ALIASES)

AUDIT_KIND = "category_split_fix_2026_06_06"


def _connect():
    """Connect to MongoDB (same pattern as backfill scripts)."""
    from database.connection import init_db, get_db, DatabaseConfig

    mongo_url = os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
    if mongo_url:
        config = DatabaseConfig.from_uri(mongo_url, database="ims_2_0")
    else:
        config = DatabaseConfig.from_env()

    if init_db(config):
        return get_db()
    return None


def _product_id_of(doc: dict) -> str:
    return str(doc.get("product_id") or doc.get("_id") or "")


def _label(doc: dict) -> str:
    """Short human label for the dry-run sample listing."""
    pid = _product_id_of(doc)
    sku = doc.get("sku", "?")
    brand = doc.get("brand", "")
    model = doc.get("model", "")
    name = (f"{brand} {model}").strip() or "(no name)"
    return f"{pid} | SKU={sku} | {name}"


def run(apply: bool) -> int:
    """Execute the category normalization. Returns exit code (0 = OK)."""
    db = _connect()
    if db is None or not db.is_connected:
        print("[ERROR] Could not connect to MongoDB. Set MONGODB_URL / MONGO_URL "
              "(or run via `railway run`). Nothing changed.")
        return 2

    products = db.get_collection("products")
    audit = db.get_collection("audit_log")
    if products is None:
        print("[ERROR] products collection unavailable. Nothing changed.")
        return 2

    # Find all products with a non-canonical category.
    alias_filter = {"category": {"$in": list(ALIAS_MAPPING.keys())}}
    matches = list(products.find(alias_filter))
    count = len(matches)

    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 70)
    print(f"Category split normalization backfill  [{mode}]")
    print("=" * 70)
    print(f"Non-canonical category products found: {count}")

    if count == 0:
        print("Nothing to normalize (idempotent: already clean). Exit 0.")
        return 0

    # Sample listing (cap at 25 lines so output stays readable on big batches).
    print("\nProducts that WILL be normalized:")
    for doc in matches[:25]:
        cur_cat = doc.get("category")
        new_cat = ALIAS_MAPPING.get(cur_cat)
        print(f"  - {_label(doc)} | {cur_cat} -> {new_cat}")
    if count > 25:
        print(f"  ... and {count - 25} more")

    if not apply:
        print(f"\n[DRY-RUN] No changes written. {count} product(s) WOULD be "
              f"normalized. Re-run with --apply to commit.")
        return 0

    # ---- APPLY ----
    updated = 0
    audited = 0
    now = datetime.utcnow()
    for doc in matches:
        pid = _product_id_of(doc)
        if not pid:
            print(f"[WARN] skipping a row with no product_id/_id: SKU={doc.get('sku')}")
            continue

        cur_cat = doc.get("category")
        new_cat = ALIAS_MAPPING.get(cur_cat)
        if not new_cat:
            print(f"[WARN] skipping {pid}: category {cur_cat} not in mapping")
            continue

        prior = {
            "category": cur_cat,
        }

        # Match on whichever id this doc actually carries.
        match_q = {"product_id": pid} if doc.get("product_id") else {"_id": doc.get("_id")}
        res = products.update_one(
            match_q,
            {"$set": {
                "category": new_cat,
                "updated_at": now,
                "updated_by": "backfill:" + AUDIT_KIND,
            }},
        )
        if getattr(res, "modified_count", 0):
            updated += 1

        # Audit-log every attempted change with prior values (reversible trail).
        if audit is not None:
            try:
                audit.insert_one({
                    "kind": AUDIT_KIND,
                    "collection": "products",
                    "product_id": pid,
                    "sku": doc.get("sku"),
                    "prior": prior,
                    "new": {
                        "category": new_cat,
                    },
                    "reason": f"Normalized non-canonical category alias to canonical form "
                              f"(analytics split: {cur_cat} vs {new_cat}).",
                    "created_at": now,
                })
                audited += 1
            except Exception as exc:
                print(f"[WARN] audit_log write failed for {pid}: {exc}")

    print(f"\n[APPLY] Updated {updated} product(s); wrote {audited} audit_log row(s) "
          f"(kind={AUDIT_KIND}).")
    print("Re-running --apply now is a no-op (idempotent).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize non-canonical product category names to canonical enum."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without this flag the script is a DRY-RUN.",
    )
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
