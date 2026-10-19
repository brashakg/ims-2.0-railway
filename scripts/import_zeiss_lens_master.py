#!/usr/bin/env python3
"""
IMS 2.0 - Import the ZEISS lens master (SRP list, April 2026) into lens_catalog
==============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHAT
----
Reads scripts/data/zeiss_lens_master_2026_04.csv (one row per sellable ZEISS
lens line: series x index x material x coating, transcribed from "ZEISS SRP
Price with ClearMind April 2026" and cross-checked against a second text
extraction of the PDF) and creates one `lens_catalog` LINE per row -- the same
collection, slug and validator the "+ New lens line" door (POST /lens-catalog,
routers/lens_catalog.py) uses:
  * every payload goes through validate_lens_catalog_payload() and
    slugify_lens_line() -- the door's own pure functions;
  * mrp = the ZEISS SRP, INR per PAIR, GST-inclusive (the PDF's own wording);
    cost_price is NOT set (an SRP list carries no cost);
  * gst_rate 5 / hsn_code 900150 = the gst_rates registry row for OPTICAL_LENS;
  * diameters, family, coating full name, page and caveats go to `notes`.
The lens_enum_config lists (brands / indexes / materials / lens_types /
coatings) are extended with the values the rows need (e.g. ZEISS, 1.59,
DIGITAL, DVP_PLATINUM_UV) -- union only, owner values never removed.

Insert-only: a lens_line_id that already exists is SKIPPED and reported,
never overwritten. Upgrades (PhotoFusion X, BlueGuard, Mirror, Polarised...)
are NOT imported -- lens_catalog has no add-on concept; they are listed in
scripts/data/zeiss_lens_upgrades_2026_04.csv for the owner's decision.

No audit_logs row is written (the chained audit log is the app's to write);
the run is traceable through `import_run_id` on every row it created and
its `lens_import_backups` document.

SAFETY CONTRACT
---------------
- DRY RUN is the DEFAULT: prints counts + 10 sample docs, writes nothing.
  Without a Mongo URI the dry run validates against the code's default enum
  lists (DEFAULT_ENUM_ITEMS) instead of the live ones.
- --apply: backs up the enum lists it will change into `lens_import_backups`
  (run id), then writes enums + lens lines, stamping `import_run_id`.
- --revert --run-id X: deletes the lens lines that run created (only those
  with no lens_stock_lines row) and puts each enum list back IF nobody edited
  it since the run (else it is left alone and reported).
- Plain pymongo, no app start-up (no init_db / ensure_indexes).

USAGE
-----
Dry run:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/import_zeiss_lens_master.py
Apply:
    ... scripts/import_zeiss_lens_master.py --apply
Undo:
    ... scripts/import_zeiss_lens_master.py --revert --run-id <id>
Connection: --mongo-uri, else MONGO_PUBLIC_URL, else MONGO_URL. No secret is printed.
"""

import argparse
import csv
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "backend"))

from api.services.lens_catalog_validation import (  # noqa: E402
    DEFAULT_ENUM_ITEMS,
    slugify_lens_line,
    validate_enum_config_payload,
    validate_lens_catalog_payload,
)

CSV_PATH = os.path.join(_REPO, "scripts", "data", "zeiss_lens_master_2026_04.csv")
CATALOG = "lens_catalog"
ENUMS = "lens_enum_config"
STOCK = "lens_stock_lines"
BACKUPS = "lens_import_backups"
ACTOR = "import_zeiss_lens_master"
SOURCE = "ZEISS SRP Price with ClearMind April 2026 (INR/pair incl. GST)"
HSN = "900150"  # gst_rates.py: OPTICAL_LENS -> ("900150", 5.0)
GST = 5.0
# CSV column -> lens_enum_config enum_id it must be a member of.
ENUM_FIELDS = (("brand", "brands"), ("index", "indexes"), ("material", "materials"),
               ("lens_type", "lens_types"), ("coating", "coatings"))


def _f(v: str) -> Optional[float]:
    return float(v) if str(v).strip() != "" else None


def load_rows(path: str = CSV_PATH) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def to_payload(row: Dict[str, str]) -> Dict[str, Any]:
    """CSV row -> the POST /lens-catalog body the door would receive."""
    notes = ["%s. Family: %s. Coating: %s." % (SOURCE, row["family"], row["coating_name"])]
    if row["diameters_mm"]:
        notes.append("Dia (mm): %s." % row["diameters_mm"])
    notes.append("Ranges are ZEISS resultant power (sph+cyl). PDF p%s." % row["pdf_page"])
    if row["notes"]:
        notes.append(row["notes"] + ".")
    p: Dict[str, Any] = {
        "brand": row["brand"], "series": row["series"], "index": float(row["index"]),
        "material": row["material"], "lens_type": row["lens_type"], "coating": row["coating"],
        "mrp": float(row["srp_inr_per_pair_incl_gst"]), "gst_rate": GST, "hsn_code": HSN,
        "notes": " ".join(notes),
    }
    if row["sph_min"].strip():
        p["sph_range"] = {"min": float(row["sph_min"]), "max": float(row["sph_max"]), "step": 0.25}
        p["cyl_range"] = {"min": -float(row["cyl_max"]), "max": 0.0, "step": 0.25}
    else:
        p["notes"] += " Power range not printed in the PDF - IMS default range applied, confirm with ZEISS."
    add_min = _f(row["add_min"])
    p["has_add"] = add_min is not None
    if add_min is not None:
        p["add_range"] = {"min": add_min, "max": _f(row["add_max"]), "step": _f(row["add_step"])}
    return p


def merged_enums(current: Dict[str, List[Any]], payloads: List[Dict[str, Any]]
                 ) -> Tuple[Dict[str, List[Any]], Dict[str, List[Any]]]:
    """Union the values the payloads need into the current enum lists.
    Returns (merged config, {enum_id: values added}). Owner values first."""
    merged = {k: list(v) for k, v in current.items()}
    added: Dict[str, List[Any]] = {}
    for field, enum_id in ENUM_FIELDS:
        items = merged.setdefault(enum_id, [])
        for p in payloads:
            v = p[field]
            have = any(round(float(x), 4) == round(v, 4) for x in items) if enum_id == "indexes" else v in items
            if not have:
                items.append(v)
                added.setdefault(enum_id, []).append(v)
    for enum_id in added:
        merged[enum_id] = validate_enum_config_payload(enum_id, merged[enum_id])
    return merged, added


def build(rows: List[Dict[str, str]], current_enums: Dict[str, List[Any]]):
    """Validate every row through the door's validator. Returns
    (docs, enum config to write, enum additions, errors)."""
    payloads = [to_payload(r) for r in rows]
    enums, added = merged_enums(current_enums, payloads)
    docs, errors = [], []
    for r, p in zip(rows, payloads):
        try:
            d = validate_lens_catalog_payload(p, enum_config=enums)
            d["lens_line_id"] = slugify_lens_line(d["brand"], d["series"], d["index"],
                                                  d["material"], d["lens_type"], d["coating"])
            docs.append(d)
        except ValueError as exc:
            errors.append("%s %s %s %s: %s" % (r["series"], r["index"], r["material"], r["coating"], exc))
    slugs = [d["lens_line_id"] for d in docs]
    if len(set(slugs)) != len(slugs):
        errors.append("duplicate lens_line_id inside the CSV")
    return docs, enums, added, errors


def live_enums(db) -> Dict[str, List[Any]]:
    """Same rule as lens_catalog.load_enum_config: DB lists win, defaults fill gaps."""
    out = {d["enum_id"]: list(d.get("items") or []) for d in db[ENUMS].find({})
           if isinstance(d.get("enum_id"), str)}
    for k, v in DEFAULT_ENUM_ITEMS.items():
        out.setdefault(k, list(v))
    return out


def apply(db, docs, enums, added, run_id: str) -> Dict[str, int]:
    now = datetime.now(timezone.utc)
    before = {d["enum_id"]: d.get("items") for d in db[ENUMS].find({"enum_id": {"$in": list(added)}})}
    db[BACKUPS].insert_one({"run_id": run_id, "at": now, "actor": ACTOR, "source": SOURCE,
                            "enum_before": before, "enum_after": {k: enums[k] for k in added},
                            "lens_line_ids": []})
    for enum_id in added:
        db[ENUMS].update_one({"enum_id": enum_id},
                             {"$set": {"items": enums[enum_id], "updated_at": now, "updated_by": ACTOR}},
                             upsert=True)
    existing = {d["lens_line_id"] for d in db[CATALOG].find(
        {"lens_line_id": {"$in": [x["lens_line_id"] for x in docs]}}, {"lens_line_id": 1})}
    created = []
    for d in docs:
        if d["lens_line_id"] in existing:
            continue
        db[CATALOG].insert_one({**d, "created_at": now, "updated_at": now,
                                "created_by": ACTOR, "import_run_id": run_id})
        created.append(d["lens_line_id"])
    db[BACKUPS].update_one({"run_id": run_id}, {"$set": {"lens_line_ids": created}})
    return {"created": len(created), "skipped_existing": len(existing)}


def revert(db, run_id: str) -> Dict[str, Any]:
    b = db[BACKUPS].find_one({"run_id": run_id})
    if not b:
        raise SystemExit("[ERROR] no backup for run %s" % run_id)
    deleted, kept = 0, []
    for lid in b.get("lens_line_ids") or []:
        if db[STOCK].count_documents({"lens_line_id": lid}):
            kept.append(lid)
            continue
        deleted += db[CATALOG].delete_one({"lens_line_id": lid, "import_run_id": run_id}).deleted_count
    restored, untouched = [], []
    for enum_id, after in (b.get("enum_after") or {}).items():
        cur = db[ENUMS].find_one({"enum_id": enum_id}) or {}
        if cur.get("items") != after:
            untouched.append(enum_id)  # edited since the run - leave the owner's list alone
            continue
        prev = (b.get("enum_before") or {}).get(enum_id)
        if prev is None:
            db[ENUMS].delete_one({"enum_id": enum_id})  # the run created it
        else:
            db[ENUMS].update_one({"enum_id": enum_id}, {"$set": {"items": prev, "updated_by": ACTOR + " revert"}})
        restored.append(enum_id)
    return {"deleted": deleted, "kept_with_stock": kept, "enums_restored": restored,
            "enums_edited_since": untouched}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Import the ZEISS lens master. Dry run by default.")
    ap.add_argument("--csv", default=CSV_PATH)
    ap.add_argument("--mongo-uri", default=None)
    ap.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--revert", action="store_true", help="undo a run (needs --run-id)")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args(argv)
    uri = args.mongo_uri or os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGO_URL")
    if (args.apply or args.revert) and not uri:
        print("[ERROR] --apply/--revert need a Mongo connection (MONGO_PUBLIC_URL / MONGO_URL / --mongo-uri)")
        return 2
    if args.revert and not args.run_id:
        print("[ERROR] --revert needs --run-id")
        return 2
    db = None
    if uri:
        from pymongo import MongoClient
        db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    if args.revert:
        print("[REVERTED] run %s: %s" % (args.run_id, json.dumps(revert(db, args.run_id))))
        return 0

    rows = load_rows(args.csv)
    current = live_enums(db) if db is not None else {k: list(v) for k, v in DEFAULT_ENUM_ITEMS.items()}
    docs, enums, added, errors = build(rows, current)
    print("CSV rows: %d  valid lens lines: %d  errors: %d  (enums from %s)"
          % (len(rows), len(docs), len(errors), "live DB" if db is not None else "code defaults"))
    for e in errors:
        print("  [INVALID] " + e)
    by_family: Dict[str, int] = {}
    for r in rows:
        by_family[r["family"]] = by_family.get(r["family"], 0) + 1
    for fam, n in by_family.items():
        print("  %-38s %d" % (fam, n))
    print("enum values to add:")
    for k, v in added.items():
        print("  %-10s %s" % (k, v))
    if db is not None:
        hit = db[CATALOG].count_documents({"lens_line_id": {"$in": [d["lens_line_id"] for d in docs]}})
        print("already in lens_catalog (would be skipped): %d" % hit)
    print("10 sample docs as they would be stored:")
    step = max(1, len(docs) // 10)
    for d in docs[::step][:10]:
        print("  " + json.dumps(d, default=str))
    if errors:
        print("[ABORT] fix the invalid rows first - nothing written.")
        return 1
    if not args.apply:
        print("DRY RUN - nothing written. Re-run with --apply to import.")
        return 0
    run_id = "zeiss-2026-04-" + uuid.uuid4().hex[:8]
    res = apply(db, docs, enums, added, run_id)
    print("[APPLIED] run %s: %s (undo: --revert --run-id %s)" % (run_id, json.dumps(res), run_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
