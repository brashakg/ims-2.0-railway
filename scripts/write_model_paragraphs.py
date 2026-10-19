#!/usr/bin/env python3
"""
IMS 2.0 - The AI paragraph of each MODEL's website listing (G2)
==============================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). Owner ruling
2026-10-18: one website listing per model -- "both SEO and website made
accordingly": the description is written for the MODEL, never one colour.

The listing body is built in code (shopify_push.listing ->
product_description.build_model_description_html); its one marketing
paragraph is a deterministic sentence until this script writes an AI one:
for every model (a lead with colours / sizes linked to it) it asks the
'Auto-fill with AI' copywriter for a MODEL paragraph
(product_description.generate_model_paragraph: model-level fields and the
NUMBER of colours / sizes only -- no colour name is sent, and an answer that
names one is thrown away) and stores it on the lead's twin as
ecom.model_paragraph, queueing the listing (the next press / scheduled sync
carries it; a first publish stays the explicit run).

DRY-RUN (DEFAULT): lists the models and whether each has a paragraph --
no AI call, no write. --apply asks the AI and writes (the old value is kept
in `model_paragraph_backup` under one run id). --model-sku limits to one lead.

USAGE
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/write_model_paragraphs.py
    ... scripts/write_model_paragraphs.py --apply [--model-sku <lead sku>]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)
from api.services import product_master as pm  # noqa: E402
from api.services.catalog_ecom import dirty_marks  # noqa: E402
from api.services.product_description import generate_model_paragraph  # noqa: E402

BACKUP = "model_paragraph_backup"


def plan(db, model_sku: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every model: its lead, twin, colours and sizes."""
    out = []
    for lead in db["products"].find({}):
        if lead.get("variant_of") or lead.get("deleted_at") or (model_sku and lead.get("sku") != model_sku):
            continue
        members = pm.family_members(db, lead["product_id"])
        if len(members) < 2:
            continue
        values, _clashes = pm.family_option_values([m for m in members if not m.get("deleted_at")])
        twin = db["catalog_products"].find_one({"id": lead.get("pim_product_id") or lead.get("product_id")})
        if twin is None:
            continue
        spec = pm.category_spec(lead.get("category"))
        out.append({
            "lead_sku": lead["sku"],
            "twin_id": twin["id"],
            "category": spec.display if spec is not None else str(lead.get("category")),
            "attributes": lead.get("attributes") or {},
            "colours": list(dict.fromkeys(v["option_color"] for v in values.values() if v["option_color"])),
            "sizes": list(dict.fromkeys(v["option_size"] for v in values.values() if v["option_size"])),
            "has_paragraph": bool((twin.get("ecom") or {}).get("model_paragraph")),
        })
    return out


async def apply(db, models: List[Dict[str, Any]]) -> Dict[str, Any]:
    run_id = uuid.uuid4().hex
    done, skipped = [], []
    for m in models:
        text = await generate_model_paragraph(m["category"], m["attributes"], m["colours"], m["sizes"])
        if not text:
            skipped.append(m["lead_sku"])
            continue
        twin = db["catalog_products"].find_one({"id": m["twin_id"]}) or {}
        db[BACKUP].insert_one({"run_id": run_id, "twin_id": m["twin_id"],
                               "before": (twin.get("ecom") or {}).get("model_paragraph"),
                               "at": datetime.now(timezone.utc)})
        db["catalog_products"].update_one(
            {"id": m["twin_id"]},
            {"$set": {"ecom.model_paragraph": text, **{f"ecom.{k}": v for k, v in dirty_marks().items()}}},
        )
        done.append(m["lead_sku"])
    return {"run_id": run_id, "written": done, "kept_deterministic": skipped}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write each model's AI paragraph. Dry-run by default.")
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--model-sku", default=None)
    args = parser.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backfill_reorder_quantity_minus1 import resolve_mongo_uri  # one resolver
    from pymongo import MongoClient

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        raise SystemExit("No Mongo connection: pass --mongo-uri or use `railway run`.")
    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    models = plan(db, args.model_sku)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] {len(models)} model(s)")
    for m in models:
        print(f"  {m['lead_sku']}: {len(m['colours'])} colour(s), {len(m['sizes'])} size(s), "
              f"paragraph {'written' if m['has_paragraph'] else 'deterministic'}")
    if args.apply:
        print(f"[APPLY] {asyncio.run(apply(db, models))}")
    else:
        print("[DRY-RUN] no AI call, nothing written; re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
