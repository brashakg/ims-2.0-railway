#!/usr/bin/env python3
"""
IMS 2.0 - What G2 would do to every LIVE model listing (read-only dry run)
=========================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). READ-ONLY: no
Shopify call, no database write -- it only asks the push's own model reader
(shopify_push.listing.model_listing) what it would send.

For every model whose LEAD is already on the website (its twin carries a
Shopify product id) and which has colours / sizes linked to it, it prints:
  * the options and every variant the listing would carry (and which are
    already on Shopify);
  * the colours HELD back (no price / MRP, no colour or size, looks the same
    as another, would add an option the listing cannot take) and the ones
    WAITING for a person (draft, switched off, PO draft, Needs review), by
    SKU with the reason;
  * the lead's web price under main's rule and under the G2 rule (one rule
    for every variant) -- a difference is flagged;
  * whether the lead has a size, and whether the lead's own row carries its
    own MRP.

USAGE
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/dry_run_model_listings.py [--sku <lead sku>]
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
from api.services import product_master as pm  # noqa: E402
from api.services.online_catalog import variant_rows_for_product  # noqa: E402
from api.services.shopify_push.listing import listing_view, model_listing, plan_summary  # noqa: E402
from api.services.shopify_push.product_input import _resolve_variant_pricing  # noqa: E402


def _positive(v: Any) -> bool:
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def report(db, lead_sku: Optional[str] = None) -> List[Dict[str, Any]]:
    """One entry per live model lead. Reads only."""
    out = []
    for lead in db["products"].find({}):
        if lead.get("variant_of") or lead.get("deleted_at") or (lead_sku and lead.get("sku") != lead_sku):
            continue
        if db["products"].find_one({"variant_of": lead.get("product_id")}) is None:
            continue
        twin = db["catalog_products"].find_one({"id": lead.get("pim_product_id") or lead.get("product_id")}) or (
            db["catalog_products"].find_one({"sku": lead.get("sku")})
        )
        if not twin or not (twin.get("ecom") or {}).get("shopify_product_id"):
            continue
        rows = variant_rows_for_product(db, twin, strict=True)
        lst = model_listing(db, twin, rows)
        if not lst or lst.get("variant_of"):
            continue
        db_lead_row = next((r for r in rows if str(r.get("sku")) == str(twin.get("sku"))), None)
        main_row = {k: v for k, v in (db_lead_row or {"sku": twin.get("sku")}).items() if k != "_own_price"}
        main_price, main_mrp = _resolve_variant_pricing(twin, main_row)
        new_row = next((r for r in lst["rows"] if str(r.get("sku")) == str(twin.get("sku"))), None)
        new_price, new_mrp = _resolve_variant_pricing(listing_view(twin, lst), new_row) if new_row else (0.0, 0.0)
        out.append({
            "lead_sku": twin.get("sku"),
            "shopify_product_id": twin["ecom"]["shopify_product_id"],
            "plan": plan_summary(lst),
            "lead_price_main": main_price,
            "lead_price_g2": new_price,
            "lead_mrp_g2": new_mrp,
            "lead_has_size": bool(pm.variant_option_values(lead)["option_size"]),
            "lead_row_has_own_mrp": bool(db_lead_row and _positive(db_lead_row.get("mrp"))),
        })
    return out


def print_report(entries: List[Dict[str, Any]]) -> None:
    print(f"[DRY-RUN] {len(entries)} live model listing(s) -- nothing sent, nothing written")
    for e in entries:
        p = e["plan"]
        flag = "" if e["lead_price_main"] == e["lead_price_g2"] else "   <-- CHANGES"
        print(f"\n{e['lead_sku']} ({e['shopify_product_id']}): {p['title']}")
        print("  options: " + ("; ".join(f"{o['name']} = {', '.join(o['values'])}" for o in p["options"]) or "none"))
        for v in p["variants"]:
            opts = " / ".join(str(v.get(o["name"])) for o in p["options"]) or "-"
            print(f"    {v['sku']}: {opts} -- {'on Shopify' if v['on_shopify'] else 'NEW'}")
        for h in p["held"]:
            print(f"  HELD {h['sku']} ({h['code']}): {h['reason']}")
        for w in p["waiting"]:
            print(f"  WAITING {w['sku']}: {w['reason']}")
        if p.get("refusal"):
            print(f"  REFUSED: {p['refusal']}")
        print(f"  lead price: main {e['lead_price_main']:.2f} -> G2 {e['lead_price_g2']:.2f} "
              f"(MRP {e['lead_mrp_g2']:.2f}){flag}")
        print(f"  lead has a size: {'yes' if e['lead_has_size'] else 'NO'}; "
              f"lead row carries its own MRP: {'yes' if e['lead_row_has_own_mrp'] else 'no'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="What G2 would send for every live model listing (read-only).")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--sku", default=None, help="Only the model led by this SKU.")
    args = parser.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    print_report(report(MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db], args.sku))
    return 0


if __name__ == "__main__":
    sys.exit(main())
