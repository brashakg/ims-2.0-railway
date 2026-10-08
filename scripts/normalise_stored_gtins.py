#!/usr/bin/env python3
"""
IMS 2.0 - Store every saved manufacturer barcode (GTIN) digits only - one time
=============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
Every door now stores a GTIN sanitised, digits only (services/gtin.py
sanitise_gtin). A value saved before that keeps the spaces or hyphens it was
typed with ('8 056597 720373', '805-6597-72037-3'), so a scan of
8056597720373 - an exact match - does not find its product.

WHAT IT TOUCHES
---------------
THREE collections, each checked by name before any read (SystemExit otherwise):

    products          attributes.gtin, attributes.upc
    catalog_products  attributes.gtin, attributes.upc, gtin
    catalog_variants  gtin

It REWRITES only a stored string that holds a separator AND is a valid GTIN
once the separators go, to that digits-only GTIN. The write's filter is the
row's id plus the value it read, so a value changed since the read is left
alone.

It REPORTS, and never changes:
  * invalid values (not a publishable GTIN, with the reason);
  * duplicates: one GTIN, in any spelling, held by more than one row of the
    same collection (for products, the legacy barcode field counts too);
  * every legacy products.barcode (main's old Manage Barcode wrote it; its
    Generate made random EAN-13s that pass the format check, so a valid one
    is still not proven to be the maker's).

USAGE
-----
Dry-run (DEFAULT - prints what it would rewrite and the report, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/normalise_stored_gtins.py

Act (only after the owner approves the dry run's counts):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/normalise_stored_gtins.py --commit

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL (the vars `railway run` injects). No secret value
is ever printed.
"""

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from api.services.gtin import classify_gtin, normalise_candidate, sanitise_gtin  # noqa: E402

# collection -> (the row's id field, the fields it rewrites, a field it only reports)
COLLECTIONS: Dict[str, Dict[str, Any]] = {
    "products": {"id": "product_id", "fix": ("attributes.gtin", "attributes.upc"), "legacy": "barcode"},
    "catalog_products": {"id": "id", "fix": ("attributes.gtin", "attributes.upc", "gtin"), "legacy": None},
    "catalog_variants": {"id": "sku", "fix": ("gtin",), "legacy": None},
}


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )


def _value(doc: Dict[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def normalise(coll, *, commit: bool) -> Dict[str, List[Any]]:
    """Print, and with ``commit`` apply, the rewrites for one collection, and
    print the report. Returns {"rewrite", "invalid", "duplicates", "legacy"}.
    Refuses (SystemExit) any collection not in COLLECTIONS. Explicit checks,
    not `assert`: `python -O` strips asserts."""
    name = getattr(coll, "name", None)
    if name not in COLLECTIONS:
        raise SystemExit(f"refusing: pointed at {name!r}, only {sorted(COLLECTIONS)!r} are allowed")
    cfg = COLLECTIONS[name]
    id_field, legacy = cfg["id"], cfg["legacy"]
    fields = list(cfg["fix"]) + ([legacy] if legacy else [])
    flt = {"$or": [{f: {"$exists": True, "$nin": [None, ""]}} for f in fields]}
    top = {f.split(".")[0] for f in fields}
    rows = list(coll.find(flt, {"_id": 0, id_field: 1, **{t: 1 for t in top}}))

    out: Dict[str, List[Any]] = {"rewrite": [], "invalid": [], "duplicates": [], "legacy": []}
    holders: Dict[str, set] = {}
    for doc in rows:
        rid = doc.get(id_field)
        for f in fields:
            raw = _value(doc, f)
            if not normalise_candidate(raw):
                continue
            clean = sanitise_gtin(raw)
            if f == legacy:
                out["legacy"].append((rid, raw, classify_gtin(raw) or "passes the format check"))
            elif not clean:
                out["invalid"].append((rid, f, raw, classify_gtin(raw)))
            elif isinstance(raw, str) and clean != raw:
                out["rewrite"].append((rid, f, raw, clean))
            if clean:
                holders.setdefault(clean.zfill(14), set()).add(rid)
    out["duplicates"] = sorted((g, sorted(map(str, ids))) for g, ids in holders.items() if len(ids) > 1)

    print(f"== {name}: {len(rows)} row(s) hold a manufacturer barcode")
    print(f"{len(out['rewrite'])} value(s) to store digits only:")
    for rid, f, raw, clean in out["rewrite"]:
        print(f"  {rid}  {f}  {raw!r} -> {clean!r}")
    print(f"{len(out['invalid'])} invalid value(s) (REPORT ONLY, left as they are):")
    for rid, f, raw, reason in out["invalid"]:
        print(f"  {rid}  {f}  {str(raw)[:40]!r}  {reason}")
    print(f"{len(out['duplicates'])} GTIN(s) held by more than one row (REPORT ONLY):")
    for g, ids in out["duplicates"]:
        print(f"  {g}  {', '.join(ids)}")
    if legacy:
        print(f"{len(out['legacy'])} legacy {name}.{legacy} value(s) (REPORT ONLY, unverified):")
        for rid, raw, verdict in out["legacy"]:
            print(f"  {rid}  {str(raw)[:40]!r}  {verdict}")
    if not commit:
        print("DRY RUN - nothing written. Re-run with --commit to store them digits only.")
        return out
    done = 0
    for rid, f, raw, clean in out["rewrite"]:
        if rid in (None, ""):
            print(f"  skipped: a row with no {id_field} ({f} {raw!r})")
            continue
        res = coll.update_one({id_field: rid, f: raw}, {"$set": {f: clean}})
        done += int(getattr(res, "modified_count", 0) or 0)
    print(f"REWROTE {done} of {len(out['rewrite'])}.")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Store saved GTINs digits only. Dry-run by default.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--commit", action="store_true", help="Rewrite the values (default: dry-run).")
    args = parser.parse_args(argv)
    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        print("No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass --mongo-uri, "
              "or run via `railway run` so the vars are injected.")
        return 2
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    for name in COLLECTIONS:
        normalise(client[args.db][name], commit=args.commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
