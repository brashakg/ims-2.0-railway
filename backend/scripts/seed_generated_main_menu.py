#!/usr/bin/env python3
"""
IMS 2.0 - Seed the self-maintaining website main menu (owner 2026-10-29)
=======================================================================
Runbook-only one-off. NOT in CI. ASCII only (Windows cp1252).

Creates the ``ecom_menus`` doc for handle ``main-menu`` flagged
``generated`` with the APPROVED template (owner 2026-10-19, Gem
Opticians-style mega menu: New Arrivals | Sunglasses panel (By Gender / By
Shape / By Price / Top 10 Brands + Shop all) | Eyeglasses (only while it has
products) | Shop by Brand A-Z | About Us with the live links), then shows
the menu IMS would generate from the collections that have products now.

DRY RUN (default): prints the template, the generated menu with product
counts and every collection left out (and why). Nothing is written.

--apply: inserts the doc (refused when a main-menu doc already exists --
edit its template on the Menus page instead) and saves the generated
items. NOTHING is pushed to Shopify here, and no scheduled sync overwrites
the hand-made website menu either: the FIRST push is a person pressing
"Regenerate & push now" on the Menus page, which adopts the existing
Shopify main-menu by its handle (taking over its gid, name and heading
URLs), backs its current tree up into ecom_menu_backups, and only then
overwrites it. After that the menu updates itself after each sync.

USAGE
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/seed_generated_main_menu.py
    ... backend/scripts/seed_generated_main_menu.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
REPO = os.path.dirname(BACKEND)
for p in (BACKEND, os.path.join(REPO, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _print_tree(items, depth=1):
    for it in items:
        count = f" ({it['count']} products)" if it.get("count") is not None else ""
        where = it.get("url") or it.get("handle") or ""
        print(f"{'  ' * depth}- {it.get('title')}{count}  {where}")
        _print_tree(it.get("children") or [], depth + 1)


def run(db, apply: bool) -> dict:
    from api.services import collection_membership, generated_menu

    existing = db[generated_menu.MENUS].find_one({"handle": generated_menu.MAIN_MENU})
    doc = generated_menu.make_template_doc()
    facts = collection_membership.online_facts(db, strict=True)  # an unreadable catalogue stops here
    collections = collection_membership.live_collections(db)
    counts = collection_membership.counts_by_handle(db, facts, collections)
    items, hidden = generated_menu.generate(doc["template"], collections, counts)

    print("TEMPLATE sections: " + " | ".join(s["title"] for s in doc["template"]["sections"]))
    print(f"\nGENERATED main-menu ({len(items)} top-level items):")
    _print_tree(items)
    print(f"\nLEFT OUT ({len(hidden)}; each comes back by itself once it has products on the website):")
    for h in hidden:
        print(f"  {h['section']}: {h['title']} [{h.get('handle') or '-'}] -- {h['reason']}")

    if existing is not None:
        print(f"\n[refused] a main-menu doc already exists (menu_id={existing.get('menu_id')}, "
              f"generated={bool(existing.get('generated'))}). Edit its template on the Menus page.")
        return {"created": False, "reason": "exists"}
    if not apply:
        print("\nDRY RUN: nothing written. Re-run with --apply to create the template doc.")
        return {"created": False, "reason": "dry-run"}
    db[generated_menu.MENUS].insert_one(dict(doc))
    doc.pop("_id", None)
    generated_menu.regenerate(db, doc, facts)
    print(f"\nCREATED menu_id={doc['menu_id']}. Nothing pushed. The first push is a person's "
          "'Regenerate & push now' on the Menus page (it adopts and backs up the live menu); "
          "after that it updates itself after each sync.")
    return {"created": True, "menu_id": doc["menu_id"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seed the generated main menu. Dry run by default.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    run(db, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
