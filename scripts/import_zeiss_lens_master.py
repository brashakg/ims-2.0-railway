#!/usr/bin/env python3
"""
IMS 2.0 - Import the ZEISS lens master (SRP list, April 2026) into lens_catalog
==============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHAT
----
Reads scripts/data/zeiss_lens_master_2026_04.csv (one row per sellable ZEISS
lens line: series x index x variant x coating, transcribed from "ZEISS SRP
Price with ClearMind April 2026" + "ZEISS WSP with ClearMind April 2026" and
cross-checked against a second text extraction of each PDF) and creates one
`lens_catalog` LINE per row -- the same
collection, slug and validator the "+ New lens line" door (POST /lens-catalog,
routers/lens_catalog.py) uses:
  * every payload goes through validate_lens_catalog_payload() and
    slugify_lens_line() -- the door's own pure functions;
  * mrp = the ZEISS SRP, INR per PAIR, GST-inclusive (owner 10-19: per pair,
    as ZEISS prints it); cost_price = the ZEISS WSP (our purchase price),
    INR per PAIR, EXCLUSIVE of GST (the WSP's own wording);
  * material = the substrate ZEISS publishes for the index (MATERIAL_BY_INDEX,
    owner 10-19); the ZEISS option the PDF prices separately (Photo Grey,
    BlueGuard, UVProtect, PhotoFusion X...) is the CSV `variant` and goes into
    the series name, so each priced line keeps its own lens_line_id;
  * upgrades = the add-ons staff may tick at billing (owner 10-19: a LIST on
    the line, never one line per combination): the family's upgrades whose
    index list covers the line, minus the combinations ZEISS rules out
    (UPGRADE_RULES), each {name, mrp (SRP), cost_price (WSP), notes};
  * gst_rate 5 / hsn_code 900150 = the gst_rates registry row for OPTICAL_LENS;
  * diameters, family, coating full name, page and caveats go to `notes`.
The lens_enum_config lists (brands / indexes / materials / lens_types /
coatings) are extended with the values the rows need (e.g. ZEISS, 1.59,
DIGITAL, DVP_PLATINUM_UV) -- union only, owner values never removed.

Insert-only: a lens_line_id that already exists is SKIPPED and reported,
never overwritten. POS does not sell lens_catalog lines yet; the upgrade list
is data for when it does.

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
import re
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
UPGRADES_PATH = os.path.join(_REPO, "scripts", "data", "zeiss_lens_upgrades_2026_04.csv")
CATALOG = "lens_catalog"
ENUMS = "lens_enum_config"
STOCK = "lens_stock_lines"
BACKUPS = "lens_import_backups"
ACTOR = "import_zeiss_lens_master"
SOURCE = "ZEISS SRP Price with ClearMind April 2026 (INR/pair incl. GST)"
COST_SOURCE = "ZEISS WSP with ClearMind April 2026 (INR/pair excl. GST)"
HSN = "900150"  # gst_rates.py: OPTICAL_LENS -> ("900150", 5.0)
GST = 5.0
# The substrate ZEISS publishes per index (owner 10-19: "material depends on
# lens index"). Sources: ZEISS ClearView lens spec 6/23 (1.50 ADC hard resin,
# 1.59 polycarbonate, 1.60 MR-8, 1.67 MR-7, 1.74 MR-174 episulphide), ZEISS
# semi-finished SV fact sheets 06/23 (1.67 in both MR-10 and MR-7),
# zeiss.co.in ("Polycarbonate 1.59"). ZEISS publishes no substrate for 1.56:
# PLASTIC_156 is ZEISS India's own "Plastic" wording until ZEISS names it.
MATERIAL_BY_INDEX = {1.5: "CR39", 1.56: "PLASTIC_156", 1.59: "POLY", 1.6: "MR8",
                     1.67: "MR7_MR10", 1.74: "MR174"}
# CSV variant -> the words appended to the series (as ZEISS names the product).
VARIANT_LABEL = {"PHOTO_GREY": "Photo Grey", "PHOTOFUSION_GREY": "PhotoFusion Grey",
                 "PHOTOFUSION_X": "PhotoFusion X", "BLUEGUARD": "BlueGuard",
                 "UVPROTECT": "UVProtect", "PHOTO": "Photo"}
# Combinations ZEISS rules out ("NOT AVAILABLE COMBINATIONS" / upgrade notes):
#   coating_has -> only on a line whose coating code contains it (Mirror,
#                  Flash, AdaptiveSun are priced "on top of Platinum");
#   coating_not -> never on such a coating (BlueProtect excludes BlueGuard,
#                  PhotoFusion X and Polarised);
#   clear_only  -> only on a CLEAR variant (no photochromic on photochromic,
#                  "Tint: clear lenses only").
# Upgrade-vs-upgrade rules (BlueGuard vs PhotoFusion X ...) stay in the notes.
UPGRADE_RULES = {
    "Mirror": {"coating_has": "PLATINUM"}, "Flash": {"coating_has": "PLATINUM"},
    "AdaptiveSun": {"coating_has": "PLATINUM"},
    "BlueGuard": {"coating_not": "BLUEPROTECT", "clear_only": True},
    "PhotoFusion X": {"coating_not": "BLUEPROTECT", "clear_only": True},
    "Polarised": {"coating_not": "BLUEPROTECT", "clear_only": True},
    "Photo": {"clear_only": True}, "Tint": {"clear_only": True},
}
# CSV column -> lens_enum_config enum_id it must be a member of.
ENUM_FIELDS = (("brand", "brands"), ("index", "indexes"), ("material", "materials"),
               ("lens_type", "lens_types"), ("coating", "coatings"))


def _f(v: str) -> Optional[float]:
    return float(v) if str(v).strip() != "" else None


def load_rows(path: str = CSV_PATH) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _parse_indexes(text: str) -> Tuple[Optional[set], set, List[str]]:
    """'1.5, 1.6 (intl), 1.67 (intl) (3Dv only)' -> ({1.5, 1.6, 1.67},
    {1.6, 1.67} international, ['3Dv']). 'all' -> (None, set(), [])."""
    if text.strip().lower() == "all":
        return None, set(), []
    idx, intl, only = set(), set(), []
    for num, paren in re.findall(r"(\d\.\d+)\*?\s*(?:\(([^)]*)\))?", text):
        idx.add(round(float(num), 2))
        if "intl" in paren:
            intl.add(round(float(num), 2))
    for paren in re.findall(r"\(([^)]*)\)", text):
        if "intl" in paren or "solid" in paren:
            continue  # international flag / colour choice, not a series limit
        only += [w.replace(" only", "").strip() for w in paren.split(",")]
    return idx, intl, only


def upgrades_for(row: Dict[str, str], upgrades: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """The family's upgrades this line may carry (see UPGRADE_RULES)."""
    index = round(float(row["index"]), 2)
    out = []
    for u in upgrades:
        if u["family"] != row["family"]:
            continue
        idx, intl, only = _parse_indexes(u["indexes"])
        if idx is not None and index not in idx:
            continue
        if only and not any(w in row["series"] for w in only):
            continue
        rule = UPGRADE_RULES.get(u["upgrade"], {})
        if rule.get("coating_has") and rule["coating_has"] not in row["coating"]:
            continue
        if rule.get("coating_not") and rule["coating_not"] in row["coating"]:
            continue
        if rule.get("clear_only") and row["variant"] != "CLEAR":
            continue
        notes = [u["notes"]] if u["notes"] else []
        if index in intl:
            notes.append("International order at this index")
        item: Dict[str, Any] = {"name": u["upgrade"], "mrp": float(u["srp_inr_per_pair_incl_gst"])}
        if u["wsp_inr_per_pair_excl_gst"].strip():
            item["cost_price"] = float(u["wsp_inr_per_pair_excl_gst"])
        if notes:
            item["notes"] = "; ".join(notes)
        out.append(item)
    return out


def to_payload(row: Dict[str, str], upgrades: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """CSV row -> the POST /lens-catalog body the door would receive."""
    index = round(float(row["index"]), 2)
    series = row["series"]
    label = VARIANT_LABEL.get(row["variant"])
    if label and label.lower() not in series.lower():
        series = "%s %s" % (series, label)
    notes = ["%s. Family: %s. Coating: %s." % (SOURCE, row["family"], row["coating_name"])]
    if row["wsp_inr_per_pair_excl_gst"].strip():
        notes.append("Cost = %s, WSP p%s." % (COST_SOURCE, row["wsp_pdf_page"]))
    if row["diameters_mm"]:
        notes.append("Dia (mm): %s." % row["diameters_mm"])
    notes.append("Ranges are ZEISS resultant power (sph+cyl). PDF p%s." % row["pdf_page"])
    if row["notes"]:
        notes.append(row["notes"] + ".")
    p: Dict[str, Any] = {
        "brand": row["brand"], "series": series, "index": float(row["index"]),
        "material": MATERIAL_BY_INDEX[index], "lens_type": row["lens_type"], "coating": row["coating"],
        "mrp": float(row["srp_inr_per_pair_incl_gst"]), "gst_rate": GST, "hsn_code": HSN,
        "notes": " ".join(notes),
        "upgrades": upgrades_for(row, upgrades or []),
    }
    if row["wsp_inr_per_pair_excl_gst"].strip():
        p["cost_price"] = float(row["wsp_inr_per_pair_excl_gst"])
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


def build(rows: List[Dict[str, str]], current_enums: Dict[str, List[Any]],
          upgrades: Optional[List[Dict[str, str]]] = None):
    """Validate every row through the door's validator. Returns
    (docs, enum config to write, enum additions, errors)."""
    if upgrades is None:
        upgrades = load_rows(UPGRADES_PATH)
    payloads = [to_payload(r, upgrades) for r in rows]
    enums, added = merged_enums(current_enums, payloads)
    docs, errors = [], []
    for r, p in zip(rows, payloads):
        try:
            d = validate_lens_catalog_payload(p, enum_config=enums)
            d["lens_line_id"] = slugify_lens_line(d["brand"], d["series"], d["index"],
                                                  d["material"], d["lens_type"], d["coating"])
            docs.append(d)
        except ValueError as exc:
            errors.append("%s %s %s %s: %s" % (r["series"], r["index"], r["variant"], r["coating"], exc))
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
    print("with cost_price (WSP excl GST): %d  without: %d  lines with upgrades: %d  upgrade entries: %d"
          % (sum("cost_price" in d for d in docs), sum("cost_price" not in d for d in docs),
             sum(bool(d.get("upgrades")) for d in docs), sum(len(d.get("upgrades") or []) for d in docs)))
    for d in docs:
        if d.get("cost_price") is not None and d["cost_price"] >= d["mrp"]:
            print("  [WARN] cost >= MRP: %s" % d["lens_line_id"])
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
