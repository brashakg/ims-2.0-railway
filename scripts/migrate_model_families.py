#!/usr/bin/env python3
"""
IMS 2.0 - One model -> its colours and sizes as variants (G1, owner 2026-10-18)
==============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHAT IT DOES
------------
Groups the catalogue by MODEL -- same category + brand + model number, by the
create door's own rule (product_master.family_key: the identity normaliser,
so 'PLD 4162/S' and 'PLD4162/S' are one model, and a brand typed in front of
the model number is folded: 'CARRERA CA PRW 5/S/IN' under Carrera is model
'CA PRW 5/S/IN') -- and links every other item of a model to its LEAD (the
first catalogued: earliest created_at, tie -> SKU) through the app's own link
writer (product_master._link_one: spine variant_of, the twin's
ecom.variant_of, and -- only while the lead is NOT on Shopify -- the
catalog_variants row with its colour + size). A model whose lead is on the
website is linked in IMS only: its rows wait for the website step (G2).

Stored model numbers and brands are never rewritten. Shape and gender (the
model-level fields that are not part of an item's identity) become the
LEAD's value on every item of the model (the lead's blank is filled with the
model's most common value) -- never on an item that is on the website (a
listing change belongs to G2; reported). Variant fields (colours, sizes, materials, lens,
tint, polarization...) are never touched. Blank eye sizes stay blank
(reported). An item with its own live website listing, a product marked
'different model', archived items, items with no model number and
categories without colours (lenses, contact lenses...) are left alone.

NEVER touches SKUs, barcodes, stock_units, orders, POs, receipts, bills or
reorder levels. Every document it writes (products, catalog_products,
catalog_variants) is copied first into `model_family_backup` under one run
id, so --revert puts them back exactly (a document the run created is
removed).

USAGE
-----
Dry-run (DEFAULT - prints the plan, writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/migrate_model_families.py
Apply:
    ... scripts/migrate_model_families.py --apply
Revert the latest applied run (or --run-id <id>):
    ... scripts/migrate_model_families.py --revert
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
from api.services import product_master as pm  # noqa: E402

BACKUP = "model_family_backup"
# The model-level fields this run settles: the ones that are not an item's
# identity (brand and model number are never rewritten).
SETTLED_FIELDS = ("shape", "gender")


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def plan(db) -> Dict[str, Any]:
    """Everything apply() would write, and every reason something is left."""
    report: Dict[str, Any] = {
        "scanned": 0,
        "brand_in_model": [],
        "model_field_changes": [],
        # On the website: its shape / gender is a listing change (G2).
        "model_field_left_live": [],
        "families": [],
        "skipped": [],
        "opted_out": 0,
    }
    products = db["products"]
    docs: List[Dict[str, Any]] = []
    for d in products.find({}):
        if _blank(d.get("product_id")) or d.get("deleted_at") or not pm.is_family_category(d.get("category")):
            continue
        report["scanned"] += 1
        if d.get("family_opt_out"):
            report["opted_out"] += 1
            continue
        key = pm.family_key(d)
        model_no = (d.get("attributes") or {}).get("model_no")
        if key and pm.normalise_identity_component(model_no) != key[2]:
            report["brand_in_model"].append({"sku": d.get("sku"), "model_no": model_no})
        docs.append(d)

    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for d in docs:
        key = pm.family_key(d)
        if key is not None:
            groups.setdefault(key, []).append(d)

    for key, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(members) < 2:
            continue
        lead = pm.lead_of(members)
        if lead is None:
            continue
        values: Dict[str, Any] = {}
        for k in SETTLED_FIELDS:
            own = (lead.get("attributes") or {}).get(k)
            if not _blank(own):
                values[k] = own
                continue
            seen = Counter(
                str((m.get("attributes") or {}).get(k)).strip()
                for m in members
                if not _blank((m.get("attributes") or {}).get(k))
            )
            if seen:
                values[k] = seen.most_common(1)[0][0]
        links, linked = [], []
        ordered = sorted(members, key=pm.lead_order)
        for m in ordered:
            attrs = m.get("attributes") or {}
            diff = {k: v for k, v in values.items() if str(attrs.get(k) or "").strip() != str(v).strip()}
            if diff and pm.has_listing(db, m):
                report["model_field_left_live"].append({"sku": m.get("sku"), "would_set": diff})
            elif diff:
                report["model_field_changes"].append(
                    {"product_id": m["product_id"], "sku": m.get("sku"), "set": diff,
                     "was": {k: attrs.get(k) for k in diff}}
                )
            if m is lead:
                continue
            if m.get("variant_of") == lead["product_id"]:
                linked.append(m["product_id"])
                continue
            if pm.has_listing(db, m):
                report["skipped"].append({"sku": m.get("sku"), "reason": "own live website listing"})
                continue
            if products.find_one({"variant_of": m["product_id"]}):
                report["skipped"].append({"sku": m.get("sku"), "reason": "leads variants of its own"})
                continue
            links.append(m["product_id"])
        options, clashes = pm.family_option_values(members)
        report["families"].append(
            {
                "key": "|".join(key),
                "lead_id": lead["product_id"],
                "lead_sku": lead.get("sku"),
                "label": " ".join(
                    str(v) for v in ((lead.get("attributes") or {}).get("brand_name"),
                                     (lead.get("attributes") or {}).get("model_no")) if v
                ) or "|".join(key[1:]),
                "members": len(members),
                "link": links,
                "already_linked": linked,
                # On the website: linked in IMS only, its rows wait for G2.
                "lead_on_website": pm.has_listing(db, lead),
                "options": [options.get(m.get("sku")) for m in ordered],
                "blank_sizes": [m.get("sku") for m in ordered if not options.get(m.get("sku"), {}).get("option_size")]
                if any(o.get("option_size") for o in options.values())
                else [],
                "clashes": clashes,
            }
        )
    return report


def _backup(db, run_id: str, coll: str, flt: Dict[str, Any], seen: set) -> None:
    tag = (coll, tuple(sorted(flt.items())))
    if tag in seen:
        return
    seen.add(tag)
    before = db[coll].find_one(flt)
    db[BACKUP].insert_one(
        {"run_id": run_id, "collection": coll, "filter": flt, "before": copy.deepcopy(before),
         "at": datetime.now(timezone.utc)}
    )


def apply(db, report: Dict[str, Any], run_id: Optional[str] = None) -> Dict[str, Any]:
    """Write the plan. Every touched document is backed up first."""
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    products, twins = db["products"], db["catalog_products"]
    seen: set = set()

    def back(pid: str) -> None:
        doc = products.find_one({"product_id": pid}) or {}
        _backup(db, run_id, "products", {"product_id": pid}, seen)
        _backup(db, run_id, "catalog_products", {"id": pm._twin_id(doc)}, seen)
        if doc.get("sku"):
            _backup(db, run_id, "catalog_variants", {"sku": doc["sku"]}, seen)

    touched = {c["product_id"] for c in report["model_field_changes"]}
    for fam in report["families"]:
        if fam["link"]:
            # The whole family: refresh_family_options rewrites every row.
            touched |= {fam["lead_id"], *fam["link"], *fam["already_linked"]}
    for pid in sorted(touched):
        back(pid)

    for c in report["model_field_changes"]:
        doc = products.find_one({"product_id": c["product_id"]}) or {}
        patch = {f"attributes.{k}": v for k, v in c["set"].items()}
        products.update_one({"product_id": c["product_id"]}, {"$set": patch})
        twin_patch = dict(patch)
        twin_patch.update({f"ecom.category_specific.{k}": v for k, v in c["set"].items()})
        twins.update_one({"id": pm._twin_id(doc)}, {"$set": twin_patch})
    linked = 0
    for fam in report["families"]:
        if not fam["link"]:
            continue
        lead = products.find_one({"product_id": fam["lead_id"]})
        for pid in fam["link"]:
            pm._link_one(db, products.find_one({"product_id": pid}), lead)
            linked += 1
        pm._settle_lead(db, lead)
    return {"run_id": run_id, "linked": linked, "backed_up": len(seen)}


def revert(db, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Put back every document a run touched (a document it created is
    removed). Latest unreverted run unless `run_id` is given."""
    backups = db[BACKUP]
    if run_id is None:
        last = backups.find_one({"reverted_at": {"$exists": False}}, sort=[("at", -1)])
        if last is None:
            return {"run_id": None, "restored": 0}
        run_id = last["run_id"]
    restored = 0
    for b in list(backups.find({"run_id": run_id, "reverted_at": {"$exists": False}})):
        coll = db[b["collection"]]
        if b.get("before") is None:
            coll.delete_one(b["filter"])
        else:
            coll.replace_one(b["filter"], b["before"], upsert=True)
        restored += 1
    backups.update_many({"run_id": run_id}, {"$set": {"reverted_at": datetime.now(timezone.utc)}})
    return {"run_id": run_id, "restored": restored}


def print_report(report: Dict[str, Any], mode: str, examples: int = 5) -> None:
    fams = report["families"]
    to_link = sum(len(f["link"]) for f in fams)
    print(f"[{mode}] scanned {report['scanned']} catalogued item(s) in colour categories")
    print(f"[{mode}] {len(fams)} model(s) with 2+ items; {to_link} item(s) to link, "
          f"{sum(len(f['already_linked']) for f in fams)} already linked")
    on_site = [f for f in fams if f["lead_on_website"] and f["link"]]
    print(f"[{mode}] models whose lead is on the website (linked in IMS only, rows wait for G2): {len(on_site)}")
    for f in on_site:
        print(f"    {f['label']} (lead {f['lead_sku']}): {len(f['link'])} item(s)")
    print(f"[{mode}] brand typed into the model number (grouped, NOT rewritten): {len(report['brand_in_model'])}")
    for b in report["brand_in_model"]:
        print(f"    {b['sku']}: {b['model_no']}")
    print(f"[{mode}] shape / gender set to the lead's value: {len(report['model_field_changes'])} item(s)")
    for c in report["model_field_changes"]:
        print(f"    {c['sku']}: {c['was']} -> {c['set']}")
    print(f"[{mode}] shape / gender NOT changed because the item is on the website (G2): "
          f"{len(report['model_field_left_live'])}")
    for c in report["model_field_left_live"]:
        print(f"    {c['sku']}: would set {c['would_set']}")
    print(f"[{mode}] left alone: {len(report['skipped'])} item(s); 'different model': {report['opted_out']}")
    for s in report["skipped"]:
        print(f"    {s['sku']}: {s['reason']}")
    blanks = [(f["label"], f["blank_sizes"]) for f in fams if f["blank_sizes"]]
    print(f"[{mode}] models with some eye sizes blank (kept blank): {len(blanks)}")
    for label, skus in blanks:
        print(f"    {label}: {', '.join(skus)}")
    clashes = [(f["label"], f["clashes"]) for f in fams if f["clashes"]]
    print(f"[{mode}] models where two items have the same colour and size: {len(clashes)}")
    for label, groups in clashes:
        print(f"    {label}: {groups}")
    for f in fams[:examples]:
        opts = "; ".join(f"{o['option_color'] or '-'} {o['option_size'] or ''}".strip() for o in f["options"] if o)
        print(f"  e.g. {f['label']} (lead {f['lead_sku']}, {f['members']} items): {opts}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Link each model's colours and sizes to it. Dry-run by default.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true", help="Write (backs up first).")
    parser.add_argument("--revert", action="store_true", help="Undo an applied run.")
    parser.add_argument("--run-id", default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    if args.revert:
        print(f"[REVERT] {revert(db, args.run_id)}")
        return 0
    report = plan(db)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print_report(report, mode)
    if args.apply:
        print(f"[APPLY] {apply(db, report)}")
    else:
        print("[DRY-RUN] nothing written; re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
