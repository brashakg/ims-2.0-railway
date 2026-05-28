"""
IMS 2.0 - Smart categorizer for blank-category products
=======================================================

A safer successor to ``backfill_uncategorized_to_frame.py``.

The blunt FRAME backfill stamped EVERY uncategorized product as FRAME / 5% /
HSN 9003. A dry-run revealed ~900 uncategorized rows, many of which are clearly
CONTACT LENSES (Acuvue, Alcon, Bausch & Lomb, Cooper Vision, Dailies, ...), not
frames. Mislabelling those as FRAME is GST-harmless (frames and contact lenses
are both 5%) but pollutes category reports / the inventory category filter.

This script classifies each blank-category product from its text (sku + brand +
model + name) and assigns the RIGHT category, deriving the (hsn, gst_rate) from
the canonical ``api/services/gst_rates.py`` so the master rate equals what POS
bills:

  - Confident contact-lens signal  -> CONTACT_LENS / COLORED_CONTACT_LENS (5%)
  - Explicit "sunglass" / "watch" / "clock" text -> SUNGLASS / WATCH (18%)
  - Everything else                -> FRAME (5%)   [the optical-dominant default]

Design choices (deliberately CONSERVATIVE):
  - We only raise a row to the 18% tier on an UNAMBIGUOUS literal keyword
    ("sunglass", "watch", "clock"). We do NOT guess 18% from brand names like
    Ray-Ban / Fastrack / Titan, which also make 5% frames -- a false positive
    there would over-charge GST on a frame. When unsure, a row stays FRAME (5%),
    which is exactly the status quo (uncategorized already bills 5%), so this
    script can never make billing worse, only better.
  - Idempotent: it only ever touches rows whose category is still blank, so a
    second --apply run is a no-op.
  - Audit-logged: every change writes an audit_log row with prior + new values.

Usage (run via Railway so it reaches the production Mongo):
  # dry run (default - prints a per-category breakdown + samples, writes nothing)
  railway run --service MongoDB bash -c \
    'MONGODB_URL="$MONGO_PUBLIC_URL" .venv/Scripts/python.exe \
     backend/scripts/categorize_uncategorized_products.py'

  # apply
  railway run --service MongoDB bash -c \
    'MONGODB_URL="$MONGO_PUBLIC_URL" .venv/Scripts/python.exe \
     backend/scripts/categorize_uncategorized_products.py --apply'
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

# Make the backend package importable whether run from repo root or backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

AUDIT_KIND = "gst_categorize_2026_05_28"

# Mongo filter for "blank category": field missing, explicitly null, empty
# string, or whitespace-only.
BLANK_CATEGORY_FILTER = {
    "$or": [
        {"category": {"$exists": False}},
        {"category": None},
        {"category": ""},
        {"category": {"$regex": r"^\s*$"}},
    ]
}

# --- Classification keyword sets (matched against UPPER-cased product text). ---
# Confident contact-lens brands / product terms. These are unambiguous: nobody
# sells an "Acuvue" frame. "TORIC" / "1 DAY" / "MONTHLY" only appear here in a
# CL context for this catalogue.
_CL_KEYWORDS = (
    "ACUVUE",
    "OASYS",
    "HYDRALUX",
    "MOIST",
    "ALCON",
    "DAILIES",
    "AIR OPTIX",
    "AIROPTIX",
    "PRECISION1",
    "PRECISION 1",
    "TOTAL1",
    "TOTAL 1",
    "BAUSCH",
    "BAUSH",
    "LOMB",
    "FLOD",
    "PUREVISION",
    "SOFLENS",
    "ULTRA",
    "COOPER VISION",
    "COOPERVISION",
    "BIOFINITY",
    "AVAIRA",
    "MYDAY",
    "MY DAY",
    "CLARITI",
    "PROCLEAR",
    "FRESHLOOK",
    "FRESH LOOK",
    "FRESHKON",
    "ASPIRE",
    "CONTACT LENS",
    "CONTACT LENSES",
    " TORIC",
    "TORIC ",
    "1 DAY",
    "1DAY",
    "ONE DAY",
    "ONEDAY",
    "FRESH LOOK",
)
# Colour-contact signal (a CL that is also coloured). Checked only AFTER a row
# already looks like a contact lens OR carries a colour-lens brand.
_COLOR_CL_KEYWORDS = (
    "COLOR",
    "COLOUR",
    "HAZEL",
    "FLOD",
    "COLORBLEND",
    "COLOURBLEND",
    "TURQUOISE",
    "AMETHYST",
    "SAPPHIRE",
    "GEMSTONE",
)
# Explicit, unambiguous 18%-tier literals only. NO brand guessing.
_SUNGLASS_KEYWORDS = ("SUNGLASS", "SUN GLASS", "SUNGLASSES", "SHADES", "GOGGLE")
_WATCH_KEYWORDS = ("WATCH", "WRISTWATCH", "WRIST WATCH")
_CLOCK_KEYWORDS = ("WALL CLOCK", "TABLE CLOCK", "CLOCK")


def _connect():
    """Connect to MongoDB the way api/main.py does (MONGODB_URL / MONGO_URL,
    else component env vars). Returns the DatabaseConnection or None."""
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


def _text_of(doc: dict) -> str:
    """Combined, upper-cased text used for keyword classification."""
    parts = [
        str(doc.get("sku") or ""),
        str(doc.get("brand") or ""),
        str(doc.get("model") or ""),
        str(doc.get("name") or ""),
        str(doc.get("product_name") or ""),
    ]
    return " ".join(parts).upper()


def _any(text: str, keywords) -> bool:
    return any(kw in text for kw in keywords)


def classify(doc: dict) -> str:
    """Return the best-guess product category for a blank-category row.

    Order matters: contact-lens detection wins first (it's the most common
    mislabel in this catalogue and unambiguous), then explicit 18% literals,
    else the FRAME default.
    """
    text = _text_of(doc)

    if _any(text, _CL_KEYWORDS):
        if _any(text, _COLOR_CL_KEYWORDS):
            return "COLORED_CONTACT_LENS"
        return "CONTACT_LENS"

    # Explicit, literal 18% items only (never brand-guessed).
    if _any(text, _SUNGLASS_KEYWORDS):
        return "SUNGLASS"
    if _any(text, _WATCH_KEYWORDS):
        return "WATCH"
    if _any(text, _CLOCK_KEYWORDS):
        return "WALL_CLOCK"

    # Optical-dominant default (unchanged status quo: blank already bills 5%).
    return "FRAME"


def _label(doc: dict) -> str:
    pid = _product_id_of(doc)
    sku = doc.get("sku", "?")
    brand = doc.get("brand", "")
    model = doc.get("model", "")
    name = (f"{brand} {model}").strip() or doc.get("name") or "(no name)"
    return f"{pid} | SKU={sku} | {name}"


def run(apply: bool) -> int:
    """Execute the categorization. Returns the process exit code (0 = OK)."""
    from api.services.gst_rates import gst_rate_for_category, hsn_for_category

    db = _connect()
    if db is None or not db.is_connected:
        print(
            "[ERROR] Could not connect to MongoDB. Set MONGODB_URL / MONGO_URL "
            "(or run via `railway run --service MongoDB`). Nothing changed."
        )
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
    print(f"Smart categorize uncategorized products  [{mode}]")
    print("=" * 70)
    print(f"Uncategorized products found: {count}")
    if count == 0:
        print("Nothing to do (idempotent: already clean). Exit 0.")
        return 0

    # Plan: bucket each doc by its classified category.
    buckets: dict[str, list] = defaultdict(list)
    for doc in matches:
        buckets[classify(doc)].append(doc)

    # Breakdown summary (ordered, with rate so the owner can sanity-check tax).
    print("\nPlanned classification (category -> count | HSN | GST):")
    order = [
        "CONTACT_LENS",
        "COLORED_CONTACT_LENS",
        "FRAME",
        "SUNGLASS",
        "WATCH",
        "WALL_CLOCK",
    ]
    for cat in order:
        docs = buckets.get(cat)
        if not docs:
            continue
        rate = gst_rate_for_category(cat)
        hsn = hsn_for_category(cat) or "?"
        print(f"  {cat:<22} {len(docs):>5}  | HSN {hsn} | {rate:g}%")
        for doc in docs[:6]:
            print(f"        - {_label(doc)}")
        if len(docs) > 6:
            print(f"        ... and {len(docs) - 6} more")

    n_18 = sum(len(buckets.get(c, [])) for c in ("SUNGLASS", "WATCH", "WALL_CLOCK"))
    print(
        f"\nSummary: {count} rows -> "
        f"{count - n_18} stay 5% (frames + contact lenses), "
        f"{n_18} move to 18% (explicit sunglass/watch/clock)."
    )

    if not apply:
        print("\n[DRY-RUN] No changes written. Re-run with --apply to commit.")
        return 0

    # ---- APPLY ----
    updated = 0
    audited = 0
    now = datetime.utcnow()
    for cat, docs in buckets.items():
        rate = gst_rate_for_category(cat)
        hsn = hsn_for_category(cat)
        for doc in docs:
            pid = _product_id_of(doc)
            if not pid:
                print(f"[WARN] skipping row with no id: SKU={doc.get('sku')}")
                continue
            prior = {
                "category": doc.get("category"),
                "gst_rate": doc.get("gst_rate"),
                "hsn_code": doc.get("hsn_code"),
            }
            set_doc = {
                "category": cat,
                "gst_rate": rate,
                "updated_at": now,
                "updated_by": "backfill:" + AUDIT_KIND,
            }
            if hsn:
                set_doc["hsn_code"] = hsn
            match_q = (
                {"product_id": pid}
                if doc.get("product_id")
                else {"_id": doc.get("_id")}
            )
            res = products.update_one(match_q, {"$set": set_doc})
            if getattr(res, "modified_count", 0):
                updated += 1

            if audit is not None:
                try:
                    audit.insert_one(
                        {
                            "kind": AUDIT_KIND,
                            "collection": "products",
                            "product_id": pid,
                            "sku": doc.get("sku"),
                            "prior": prior,
                            "new": {
                                "category": cat,
                                "gst_rate": rate,
                                "hsn_code": hsn,
                            },
                            "reason": (
                                "Blank-category product classified by name/SKU "
                                "into its optical category (GST master == billing)."
                            ),
                            "created_at": now,
                        }
                    )
                    audited += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[WARN] audit_log write failed for {pid}: {exc}")

    print(
        f"\n[APPLY] Updated {updated} product(s); wrote {audited} audit_log "
        f"row(s) (kind={AUDIT_KIND})."
    )
    print("Re-running --apply now is a no-op (idempotent).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify blank-category products into their optical category "
            "(contact lens / frame / sunglass / watch) with the matching "
            "HSN + GST rate. DRY-RUN unless --apply is passed."
        )
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
