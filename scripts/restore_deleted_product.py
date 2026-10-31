"""Put a permanently deleted product back from its deleted_products_backup row.

The Catalog page's "Delete permanently" (#1273, services/product_purge) copies
every record it deletes or changes into `deleted_products_backup` FIRST. This
is the way back, for the rare mistake or a delete that stopped part-way:

  * every backed-up doc that is missing now is inserted again, exactly as it
    was (the product comes back ARCHIVED -- it was archived when deleted);
  * the records it was pulled out of get it back: its SKU in a collection's
    manual member list, its id in flag tasks, its stock baseline on the
    model's listing;
  * the delete's claim marks are dropped and the queue flag it had is given
    back;
  * when its website listing was removed, the dead Shopify ids are dropped
    (product_purge._forget_shopify) -- the listing itself is NOT recreated:
    publish it again from Online Store if it should be on the website.

Never overwrites: a product, catalogue row or variant row that exists again
under the same id, SKU or identity key refuses the whole restore.

Dry run by default (prints the plan, writes nothing):

    railway run --service MongoDB -- .venv\\Scripts\\python.exe scripts/restore_deleted_product.py --backup-id DPB-XXXX
    ... --backup-id DPB-XXXX --commit

Connection: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI, else
MONGODB_URL / MONGO_URL (the vars `railway run` injects). No secret is printed.
No emoji (Windows cp1252).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

BACKUPS = "deleted_products_backup"
_CLAIM_FIELDS = ("purge_run", "purge_run_at", "purge_prior_queue")


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )


def _unclaimed(coll: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """The doc as it was before the delete's claim touched it."""
    out = dict(doc)
    prior = out.pop("purge_prior_queue", None)
    for k in _CLAIM_FIELDS:
        out.pop(k, None)
    if coll == "catalog_products" and prior is not None and isinstance(out.get("ecom"), dict):
        out["ecom"] = {**out["ecom"], "locally_modified": prior}
    return out


def _conflicts(db, docs: Dict[str, List[Dict]]) -> List[str]:
    """Rows that exist again under the same key (never overwritten)."""
    out: List[str] = []
    for p in docs.get("products") or []:
        if db["products"].find_one({"_id": {"$ne": p["_id"]}, "product_id": p.get("product_id")}):
            out.append(f"a product with id {p.get('product_id')} exists again")
        if p.get("sku") and db["products"].find_one({"_id": {"$ne": p["_id"]}, "sku": p["sku"]}):
            out.append(f"a product with SKU {p['sku']} exists again")
        if p.get("identity_key") and db["products"].find_one(
            {"_id": {"$ne": p["_id"]}, "identity_key": p["identity_key"]}
        ):
            out.append("a product with the same brand / model / colour / size exists again")
    for t in docs.get("catalog_products") or []:
        if db["catalog_products"].find_one({"_id": {"$ne": t["_id"]}, "id": t.get("id")}):
            out.append(f"a catalogue row with id {t.get('id')} exists again")
    for v in docs.get("catalog_variants") or []:
        if db["catalog_variants"].find_one({"_id": {"$ne": v["_id"]}, "sku": v.get("sku")}):
            out.append(f"a website variant row for SKU {v.get('sku')} exists again")
    return out


def restore(db, backup_id: str, *, commit: bool = False) -> Dict[str, Any]:
    """The restore plan ({inserts, give_back, refused}); with `commit`, done."""
    backup = db[BACKUPS].find_one({"backup_id": backup_id})
    if backup is None:
        return {"refused": [f"no backup {backup_id}"]}
    if backup.get("status") == "restored":
        return {"refused": [f"backup {backup_id} was already restored"]}
    if backup.get("status") not in ("done", "deleting"):
        # prepared / removing / removed / aborted: no IMS record was deleted.
        return {"refused": [f"backup {backup_id} is {backup.get('status')!r}: nothing in IMS was deleted"]}
    docs: Dict[str, List[Dict]] = backup.get("docs") or {}
    updated: Dict[str, List[Dict]] = backup.get("updated_docs") or {}
    pid = backup.get("product_id")
    skus = sorted({d.get("sku") for d in (docs.get("products") or []) + (docs.get("catalog_products") or [])
                   if d.get("sku")} | ({backup["sku"]} if backup.get("sku") else set()))
    refused = _conflicts(db, docs)
    inserts = {
        coll: [d for d in rows if db[coll].find_one({"_id": d["_id"]}) is None]
        for coll, rows in docs.items()
    }
    give_back: List[str] = []
    for c in updated.get("ecom_collections") or []:
        mine = [e for e in (c.get("products") or []) if isinstance(e, dict) and e.get("sku") in skus]
        if mine:
            give_back.append(f"collection {c.get('collection_id') or c['_id']}: {len(mine)} member(s)")
    for t in updated.get("tasks") or []:
        give_back.append(f"task {t.get('task_id') or t['_id']}: product id")
    for parent in updated.get("catalog_products") or []:
        give_back.append(f"model listing {parent.get('id')}: stock baseline")
    plan = {
        "backup_id": backup_id,
        "product_id": pid,
        "skus": skus,
        "inserts": {c: len(r) for c, r in inserts.items() if r},
        "give_back": give_back,
        "refused": refused,
        "website": "removed -- publish it again from Online Store if it should be sold online"
        if ((backup.get("shopify") or {}).get("result") or {}).get("ok") else "not on the website",
    }
    if refused or not commit:
        return plan
    for coll, rows in inserts.items():
        for d in rows:
            db[coll].insert_one(_unclaimed(coll, d))
    for c in updated.get("ecom_collections") or []:
        for e in (c.get("products") or []):
            if isinstance(e, dict) and e.get("sku") in skus:
                db["ecom_collections"].update_one(
                    {"_id": c["_id"], "products.sku": {"$ne": e["sku"]}}, {"$push": {"products": e}}
                )
    for t in updated.get("tasks") or []:
        db["tasks"].update_one({"_id": t["_id"]}, {"$addToSet": {"payload.product_ids": pid}})
    for parent in updated.get("catalog_products") or []:
        qty = (((parent.get("ecom") or {}).get("online_stock") or {}).get("quantities")) or {}
        for s in skus:
            if s in qty:
                key = f"ecom.online_stock.quantities.{s}"
                # Only where it is still missing: a newer baseline stays.
                db["catalog_products"].update_one(
                    {"_id": parent["_id"], key: {"$exists": False}}, {"$set": {key: qty[s]}}
                )
    shopify = backup.get("shopify") or {}
    if (shopify.get("result") or {}).get("ok") and shopify.get("plan"):
        from api.services.product_purge import _forget_shopify

        twin_id = ((docs.get("catalog_products") or [{}])[0]).get("id")
        _forget_shopify(db, twin_id, shopify["plan"], spine_id=pid)
    db[BACKUPS].update_one(
        {"backup_id": backup_id},
        {"$set": {"status": "restored", "restored_at": datetime.now(timezone.utc)}},
    )
    plan["committed"] = True
    return plan


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Restore a permanently deleted product. Dry-run by default.")
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--commit", action="store_true", help="Write (default: dry-run).")
    args = parser.parse_args(argv)
    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        print("No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass --mongo-uri, "
              "or run via `railway run` so the vars are injected.")
        return 2
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=10000)[args.db]
    plan = restore(db, args.backup_id, commit=args.commit)
    for k, v in plan.items():
        print(f"{k}: {v}")
    if plan.get("refused"):
        print("REFUSED: nothing was written.")
        return 1
    if not args.commit:
        print("Dry run: nothing was written. Add --commit to restore.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
