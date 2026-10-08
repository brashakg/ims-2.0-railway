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

It REWRITES only a stored value that is a valid GTIN once read as a digit
string but is not stored as one - it holds a separator, or it was saved as a
NUMBER - to that digits-only string. The write's filter is the row's id plus
the value it read, so a value changed since the read is left alone.

It FOLDS every attribute KEY that names a barcode in another letter case or
with padding ('GTIN', 'Upc', ' gtin ') onto gtin / upc and removes the other
spelling (services/gtin.py fold_barcode_spellings, the rule every write door
now applies): main's PUT stored keys as sent, no screen shows one, the
one-holder check cannot see one, a clone would carry it, and it publishes as
ims.gtin / ims.upc when the exact key is absent. The exact key wins; a junk
value under another spelling is dropped. The write's filter is the row's id
plus the whole attributes as read.

It REPORTS, and never changes:
  * invalid values (not a publishable GTIN, with the reason);
  * duplicates: one GTIN, in any spelling, held by more than one row of the
    same collection (for products, the legacy barcode field counts too);
  * every legacy products.barcode (main's old Manage Barcode wrote it; its
    Generate made random EAN-13s that pass the format check, so a valid one
    is still not proven to be the maker's);
  * every catalog_products.barcode / catalog_variants.barcode (the Shopify
    push falls back to it when the row has no gtin);
  * each barcode key in another spelling, with its verdict (its value counts
    towards the duplicates).

USAGE
-----
Dry-run (DEFAULT - prints what it would rewrite and fold, and the report;
writes nothing):
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

from api.services.gtin import (  # noqa: E402
    classify_gtin,
    fold_barcode_spellings,
    manufacturer_barcode_key,
    normalise_candidate,
    sanitise_gtin,
)

# collection -> (the row's id field, the fields it rewrites, a field it only reports)
COLLECTIONS: Dict[str, Dict[str, Any]] = {
    "products": {"id": "product_id", "fix": ("attributes.gtin", "attributes.upc"), "legacy": "barcode"},
    "catalog_products": {"id": "id", "fix": ("attributes.gtin", "attributes.upc", "gtin"), "legacy": "barcode"},
    "catalog_variants": {"id": "sku", "fix": ("gtin",), "legacy": "barcode"},
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


def _barcode_keys(attrs: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in attrs.items() if manufacturer_barcode_key(k)}


def normalise(coll, *, commit: bool) -> Dict[str, List[Any]]:
    """Print, and with ``commit`` apply, the rewrites for one collection, and
    print the report. Returns {"rewrite", "fold", "invalid", "duplicates",
    "legacy", "spelling"}.
    Refuses (SystemExit) any collection not in COLLECTIONS. Explicit checks,
    not `assert`: `python -O` strips asserts."""
    name = getattr(coll, "name", None)
    if name not in COLLECTIONS:
        raise SystemExit(f"refusing: pointed at {name!r}, only {sorted(COLLECTIONS)!r} are allowed")
    cfg = COLLECTIONS[name]
    id_field, legacy = cfg["id"], cfg["legacy"]
    fields = list(cfg["fix"]) + ([legacy] if legacy else [])
    top = {f.split(".")[0] for f in fields}
    ors = [{f: {"$exists": True, "$nin": [None, ""]}} for f in fields]
    if "attributes" in top:
        # A key in another spelling ('GTIN') has no fixed path to query by.
        # ponytail: reads every row's attributes; fine at catalogue size.
        ors.append({"attributes": {"$exists": True}})
    rows = list(coll.find({"$or": ors}, {"_id": 0, id_field: 1, **{t: 1 for t in top}}))

    out: Dict[str, List[Any]] = {
        "rewrite": [], "fold": [], "invalid": [], "duplicates": [], "legacy": [], "spelling": []
    }
    holders: Dict[str, set] = {}
    held = 0
    for doc in rows:
        rid = doc.get(id_field)
        attrs = doc.get("attributes") if "attributes" in top else None
        others = [
            (k, raw)
            for k, raw in (attrs.items() if isinstance(attrs, dict) else ())
            if manufacturer_barcode_key(k) not in (None, k)
        ]
        if others:
            out["fold"].append((rid, attrs, fold_barcode_spellings(attrs)))
        for k, raw in others:
            verdict = classify_gtin(raw) or ("valid" if normalise_candidate(raw) else "blank")
            out["spelling"].append((rid, f"attributes.{k}", raw, verdict))
            clean = sanitise_gtin(raw)
            if clean:
                holders.setdefault(clean.zfill(14), set()).add(rid)
        found = any(normalise_candidate(raw) for _, raw in others)
        for f in fields:
            raw = _value(doc, f)
            if not normalise_candidate(raw):
                continue
            found = True
            clean = sanitise_gtin(raw)
            if f == legacy:
                out["legacy"].append((rid, raw, classify_gtin(raw) or "passes the format check"))
            elif not clean:
                out["invalid"].append((rid, f, raw, classify_gtin(raw)))
            elif clean != raw:
                out["rewrite"].append((rid, f, raw, clean))
            if clean:
                holders.setdefault(clean.zfill(14), set()).add(rid)
        held += found
    out["duplicates"] = sorted((g, sorted(map(str, ids))) for g, ids in holders.items() if len(ids) > 1)

    print(f"== {name}: {held} row(s) hold a manufacturer barcode")
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
    if "attributes" in top:
        print(f"{len(out['spelling'])} barcode key(s) in another spelling (no screen shows one):")
        for rid, k, raw, verdict in out["spelling"]:
            print(f"  {rid}  {k!r}  {str(raw)[:40]!r}  {verdict}")
        print(f"{len(out['fold'])} row(s) to fold them onto gtin / upc:")
        for rid, before, after in out["fold"]:
            print(f"  {rid}  {_barcode_keys(before)!r} -> {_barcode_keys(after)!r}")
    if not commit:
        print("DRY RUN - nothing written. Re-run with --commit to fold the keys and store them digits only.")
        return out
    # Fold first: its filter is the whole attributes as read, which a rewrite
    # below would change; a fold leaves the exact keys' values as they were.
    folded = 0
    for rid, before, after in out["fold"]:
        if rid in (None, ""):
            print(f"  skipped: a row with no {id_field} ({_barcode_keys(before)!r})")
            continue
        res = coll.update_one({id_field: rid, "attributes": before}, {"$set": {"attributes": after}})
        folded += int(getattr(res, "modified_count", 0) or 0)
    print(f"FOLDED {folded} of {len(out['fold'])}.")
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
