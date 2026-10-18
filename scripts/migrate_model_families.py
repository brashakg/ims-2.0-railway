#!/usr/bin/env python3
"""
IMS 2.0 - One model -> its colours and sizes as variants (G1, owner 2026-10-18)
==============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHAT IT DOES
------------
Groups the catalogue by MODEL -- same category + brand + model number, by the
create door's own rule (product_master.family_key: the identity normaliser,
so 'PLD 4162/S' and 'PLD4162/S' are one model) -- and links every other item
of a model to its LEAD (the first catalogued: earliest created_at, tie -> SKU)
through the app's own link writer (spine variant_of, the twin's
ecom.variant_of, the catalog_variants row with its colour + size). Before
grouping it:
  - strips a brand typed into the model ('CARRERA CA PRW 5/S/IN' under brand
    Carrera -> 'CA PRW 5/S/IN'), re-keying the item; refused (reported) when
    the new key is another product's;
  - makes the model-level fields (brand, model no, model name, shape, gender:
    product_master.MODEL_LEVEL_FIELDS) the LEAD's value on every item of the
    model (the lead's blank is filled with the model's most common value).
    Variant fields (colours, sizes, materials, lens, tint, polarization...)
    are never touched.
Blank eye sizes stay blank (reported). An item with its own live website
listing, a product marked 'different model', archived items and categories
without colours (lenses, contact lenses...) are left alone (reported).

NEVER touches SKUs, barcodes, stock_units, orders, POs, receipts, bills or
reorder levels. Every document it writes (products, catalog_products,
catalog_variants) is copied first into `model_family_backup` under one run
id, so --revert puts them back exactly.

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
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
from api.services import product_master as pm  # noqa: E402

BACKUP = "model_family_backup"
_SEP = r"[\s\-_/.]"


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def strip_brand_prefix(brand: Any, model: Any) -> Optional[str]:
    """`model` without a leading copy of `brand` ('CARRERA CA PRW 5/S/IN' ->
    'CA PRW 5/S/IN'), or None when it has none. The brand must be followed by
    a separator, so 'TITANIUM 12' under Titan is never cut."""
    nb = pm.normalise_identity_component(brand)
    if not nb or _blank(model):
        return None
    pattern = r"^\s*" + (_SEP + "*").join(map(re.escape, nb)) + _SEP + "+"
    rest = re.sub(pattern, "", str(model), count=1, flags=re.IGNORECASE)
    if rest == str(model) or not pm.normalise_identity_component(rest):
        return None
    return rest.strip()


def _key_of(doc: Dict[str, Any]) -> Optional[str]:
    return pm.compute_identity_key(*pm.identity_parts(doc.get("attributes") or {}, doc.get("category")))


def _with_attrs(doc: Dict[str, Any], attrs: Dict[str, Any]) -> Dict[str, Any]:
    """`doc` with new attributes and the columns derived from them."""
    out = dict(doc, attributes=attrs)
    ids = pm._derive_brand_model_color_size(attrs, doc.get("category"))
    out["brand"], out["model"] = ids["brand"] or doc.get("brand"), ids["model"] or doc.get("model")
    return out


def plan(db) -> Dict[str, Any]:
    """Everything apply() would write, and every reason something is left."""
    report: Dict[str, Any] = {
        "scanned": 0,
        "brand_prefix_fixes": [],
        "brand_prefix_refused": [],
        "model_field_changes": [],
        "families": [],
        "skipped": [],
        "opted_out": 0,
    }
    products = db["products"]
    by_key_owner = {d.get("identity_key"): d.get("product_id") for d in products.find({"identity_key": {"$exists": True}})}
    docs: List[Dict[str, Any]] = []
    for d in products.find({}):
        if _blank(d.get("product_id")) or d.get("deleted_at") or not pm.is_family_category(d.get("category")):
            continue
        report["scanned"] += 1
        attrs = dict(d.get("attributes") or {})
        changed = {}
        brand = attrs.get("brand_name") or d.get("brand")
        for k in ("model_no", "model_name"):
            fixed = strip_brand_prefix(brand, attrs.get(k))
            if fixed is not None:
                changed[k] = fixed
        if changed:
            new = _with_attrs(d, {**attrs, **changed})
            key = _key_of(new)
            owner = by_key_owner.get(key)
            if key and owner not in (None, d.get("product_id")):
                report["brand_prefix_refused"].append(
                    {"sku": d.get("sku"), "model": attrs.get("model_no") or attrs.get("model_name"), "clashes_with": owner}
                )
            else:
                report["brand_prefix_fixes"].append(
                    {"product_id": d["product_id"], "sku": d.get("sku"), "set": changed, "identity_key": key}
                )
                d = dict(new, identity_key=key or d.get("identity_key"))
        if d.get("family_opt_out"):
            report["opted_out"] += 1
            continue
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
        # Model-level fields: the lead's value, else the model's most common.
        values: Dict[str, Any] = {}
        for k in pm.MODEL_LEVEL_FIELDS:
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
        links, linked, model_changes = [], [], []
        twins = db["catalog_products"]
        for m in sorted(members, key=pm.lead_order):
            attrs = dict(m.get("attributes") or {})
            diff = {k: v for k, v in values.items() if str(attrs.get(k) or "").strip() != str(v).strip()}
            if diff and _key_of(_with_attrs(m, {**attrs, **diff})) != _key_of(m):
                diff = {}  # never re-key an item here (the normaliser said same model)
            if diff:
                model_changes.append(
                    {"product_id": m["product_id"], "sku": m.get("sku"), "set": diff,
                     "was": {k: attrs.get(k) for k in diff}}
                )
            if m is lead:
                continue
            if m.get("variant_of") == lead["product_id"]:
                linked.append(m["product_id"])
                continue
            twin = twins.find_one({"id": pm._twin_id(m)}) or {}
            if (twin.get("ecom") or {}).get("shopify_product_id"):
                report["skipped"].append({"sku": m.get("sku"), "reason": "own live website listing"})
                continue
            if products.find_one({"variant_of": m["product_id"]}):
                report["skipped"].append({"sku": m.get("sku"), "reason": "leads variants of its own"})
                continue
            links.append(m["product_id"])
        report["model_field_changes"] += model_changes
        options, clashes = pm.family_option_values(members)
        report["families"].append(
            {
                "key": "|".join(key),
                "lead_id": lead["product_id"],
                "lead_sku": lead.get("sku"),
                "label": " ".join(str(values.get(k) or "") for k in ("brand_name", "model_no")).strip()
                or "|".join(key[1:]),
                "members": len(members),
                "link": links,
                "already_linked": linked,
                "options": [options.get(m.get("sku")) for m in sorted(members, key=pm.lead_order)],
                "blank_sizes": [m.get("sku") for m in members if not options.get(m.get("sku"), {}).get("option_size")]
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

    def back(pid: str) -> Dict[str, Any]:
        doc = products.find_one({"product_id": pid}) or {}
        _backup(db, run_id, "products", {"product_id": pid}, seen)
        _backup(db, run_id, "catalog_products", {"id": pm._twin_id(doc)}, seen)
        if doc.get("sku"):
            _backup(db, run_id, "catalog_variants", {"sku": doc["sku"]}, seen)
        return doc

    touched = {f["product_id"] for f in report["brand_prefix_fixes"]} | {
        c["product_id"] for c in report["model_field_changes"]
    }
    for fam in report["families"]:
        if fam["link"]:
            # The whole family: refresh_family_options rewrites every row.
            touched |= {fam["lead_id"], *fam["link"], *fam["already_linked"]}
    for pid in sorted(touched):
        back(pid)

    def set_attrs(pid: str, values: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> None:
        doc = products.find_one({"product_id": pid}) or {}
        new = _with_attrs(doc, {**(doc.get("attributes") or {}), **values})
        patch = {f"attributes.{k}": v for k, v in values.items()}
        patch.update({"brand": new["brand"], "model": new["model"], **(extra or {})})
        products.update_one({"product_id": pid}, {"$set": patch})
        twin_patch = {f"attributes.{k}": v for k, v in values.items()}
        twin_patch.update({f"ecom.category_specific.{k}": v for k, v in values.items()})
        twin_patch.update({"brand": new["brand"], "model": new["model"]})
        twins.update_one({"id": pm._twin_id(doc)}, {"$set": twin_patch})

    for f in report["brand_prefix_fixes"]:
        set_attrs(f["product_id"], f["set"], {"identity_key": f["identity_key"]} if f["identity_key"] else None)
    for c in report["model_field_changes"]:
        set_attrs(c["product_id"], c["set"])
    linked = 0
    for fam in report["families"]:
        lead = products.find_one({"product_id": fam["lead_id"]})
        for pid in fam["link"]:
            pm._link_one(db, products.find_one({"product_id": pid}), lead)
            linked += 1
        if fam["link"]:
            pm._write_row(db, lead, None)
            pm.refresh_family_options(db, fam["lead_id"])
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
    print(f"[{mode}] brand typed into the model: {len(report['brand_prefix_fixes'])} fixed, "
          f"{len(report['brand_prefix_refused'])} refused (would duplicate)")
    for f in report["brand_prefix_fixes"]:
        print(f"    fix {f['sku']}: {f['set']}")
    for f in report["brand_prefix_refused"]:
        print(f"    REFUSED {f['sku']} ({f['model']}): the stripped model is product {f['clashes_with']}")
    print(f"[{mode}] model-level fields set to the lead's value: {len(report['model_field_changes'])} item(s)")
    for c in report["model_field_changes"]:
        print(f"    {c['sku']}: {c['was']} -> {c['set']}")
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
