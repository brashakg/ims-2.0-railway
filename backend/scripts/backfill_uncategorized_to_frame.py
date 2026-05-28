"""
IMS 2.0 - One-time backfill: uncategorized products -> FRAME / 5% GST
=====================================================================
QA found an uncategorized product ("Fastrack P357BK1", blank category) billed at
18% GST at POS, because the GST fallback used to be 18%. Two fixes shipped with
this script:

  1. services/gst_rates.py default fallback changed 18% -> 5% (optical-dominant).
  2. routers/products.py now blocks save when category is blank (422).

This script closes the third gap: EXISTING rows that already have a blank /
null / missing category. The owner decision (locked) is to set them to the
dominant optical category FRAME (HSN 9003, 5% GST), which is overwhelmingly the
right call for an optical chain and matches the new create-time default.

SAFETY
------
- DRY-RUN BY DEFAULT. Prints exactly what WOULD change and exits without
  touching anything. Pass --apply to actually write.
- IDEMPOTENT. A backfilled row gets category="FRAME", so it no longer matches
  the blank-category query -> a second --apply run is a no-op (0 updated).
- AUDIT-LOGGED. Every changed product writes one row to the `audit_log`
  collection with kind="gst_backfill_2026_05_28" capturing the prior
  category / gst_rate / hsn_code so the change is fully reversible by hand.
- FAIL-LOUD on no DB. If MongoDB is unreachable the script prints an error and
  exits non-zero rather than silently doing nothing.

USAGE
-----
  # dry run (default - shows count + sample, writes nothing):
  railway run .venv\\Scripts\\python.exe backend/scripts/backfill_uncategorized_to_frame.py

  # apply for real:
  railway run .venv\\Scripts\\python.exe backend/scripts/backfill_uncategorized_to_frame.py --apply

See docs/GST_BACKFILL_RUNBOOK.md for the full runbook.
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

# Target values for an uncategorized product (owner decision: FRAME / 9003 / 5%).
TARGET_CATEGORY = "FRAME"
TARGET_GST_RATE = 5.0
TARGET_HSN_CODE = "9003"
AUDIT_KIND = "gst_backfill_2026_05_28"

# Mongo filter for "blank category": field missing, explicitly null, empty
# string, or whitespace-only. (^\s*$ also matches the empty string.)
BLANK_CATEGORY_FILTER = {
    "$or": [
        {"category": {"$exists": False}},
        {"category": None},
        {"category": ""},
        {"category": {"$regex": r"^\s*$"}},
    ]
}


def _connect():
    """Connect to MongoDB exactly the way api/main.py does (MONGODB_URL /
    MONGO_URL, else component env vars). Returns the DatabaseConnection or None."""
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
    """Execute the backfill. Returns the process exit code (0 = OK)."""
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

    matches = list(products.find(BLANK_CATEGORY_FILTER))
    count = len(matches)

    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 70)
    print(f"GST uncategorized -> {TARGET_CATEGORY} backfill  [{mode}]")
    print("=" * 70)
    print(f"Uncategorized products found: {count}")

    if count == 0:
        print("Nothing to backfill (idempotent: already clean). Exit 0.")
        return 0

    # Sample listing (cap at 25 lines so output stays readable on big batches).
    print("\nProducts that WILL be set to "
          f"category={TARGET_CATEGORY}, gst_rate={TARGET_GST_RATE}, hsn_code={TARGET_HSN_CODE}:")
    for doc in matches[:25]:
        prior_cat = repr(doc.get("category"))
        prior_gst = doc.get("gst_rate")
        print(f"  - {_label(doc)} | prior category={prior_cat} gst_rate={prior_gst}")
    if count > 25:
        print(f"  ... and {count - 25} more")

    if not apply:
        print(f"\n[DRY-RUN] No changes written. {count} product(s) WOULD be "
              f"backfilled. Re-run with --apply to commit.")
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

        prior = {
            "category": doc.get("category"),
            "gst_rate": doc.get("gst_rate"),
            "hsn_code": doc.get("hsn_code"),
        }

        # Match on whichever id this doc actually carries.
        match_q = {"product_id": pid} if doc.get("product_id") else {"_id": doc.get("_id")}
        res = products.update_one(
            match_q,
            {"$set": {
                "category": TARGET_CATEGORY,
                "gst_rate": TARGET_GST_RATE,
                "hsn_code": TARGET_HSN_CODE,
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
                        "category": TARGET_CATEGORY,
                        "gst_rate": TARGET_GST_RATE,
                        "hsn_code": TARGET_HSN_CODE,
                    },
                    "reason": "Uncategorized product defaulted to FRAME/5% GST "
                              "(QA: uncategorized billed at 18%).",
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
        description="Backfill blank-category products to FRAME / 5% GST / HSN 9003."
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
